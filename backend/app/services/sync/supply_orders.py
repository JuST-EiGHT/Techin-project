"""Синхронизация заявок на поставку FBO (шаг 10.18): список, статусы, таймслоты, состав и акты приёмки.

- Список — `/v3/supply-order/list` (все статусы, новые первыми, по 100, курсор `last_id`), подробности —
  `/v3/supply-order/get` (по 50 заявок): номер, даты, статус, таймслот, пункт отгрузки, склад хранения и кластер.
  Водителя и машину (`/v1/supply-order/details`) не запрашиваем — это персональные данные.
- Состав — `/v1/supply-order/bundle` (одна поставка за вызов, по 100 товаров); акты приёмки — `/v1/supply-order/act/
  product/get` (бета): заявлено, принято, согласовано, брак. Сбой актов не роняет синхронизацию — их подтянет следующая.
- Кластер склада хранения — по нашим складам из остатков (`warehouses.cluster_name`), иначе по `/v1/cluster/list`.
- История (правило 12): смена статуса или таймслота — новая запись `supply_order_events`, изменённый состав или акт —
  новый снимок `supply_order_items`. Заявки в итоговом статусе с загруженными составом и актами не перечитываем.
- Итоговые заявки старше HISTORY_DAYS (по дате создания) — без состава и актов: первая загрузка не тянет всю историю
  кабинета; статус и даты у них есть.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import supply_orders as engine
from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import OzonAPIError, OzonAuthError, OzonNotConfiguredError
from app.models.product import Product
from app.models.supply import ACT, BUNDLE, SupplyOrder, SupplyOrderEvent, SupplyOrderItem
from app.models.warehouse import FBO, Warehouse
from app.services.supply import cluster_key

log = logging.getLogger(__name__)

LIST_PATH = "/v3/supply-order/list"
GET_PATH = "/v3/supply-order/get"
BUNDLE_PATH = "/v1/supply-order/bundle"
ACT_PATH = "/v1/supply-order/act/product/get"
CLUSTERS_PATH = "/v1/cluster/list"
PAGE = 100  # максимум limit в списке и составе
MAX_PAGES = 20  # до 2 000 заявок
GET_CHUNK = 50  # максимум order_ids в /v3/supply-order/get
MAX_BUNDLE_PAGES = 50
HISTORY_DAYS = 180  # итоговые заявки старше — без состава и актов


@dataclass
class SupplySyncStats:
    orders: int = 0  # заявок в Ozon
    fetched: int = 0  # перечитали подробности
    new: int = 0
    state_changes: int = 0  # новые записи истории (статус или таймслот)
    bundles: int = 0  # новые снимки состава
    acts: int = 0  # новые снимки актов
    acts_failed: int = 0  # акты не отдались (бета) — подтянем в следующий раз
    unknown_products: int = 0  # товаров нет в каталоге

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


async def fetch_ids(client: OzonClient) -> list[int]:
    ids: list[int] = []
    last_id = ""
    for _ in range(MAX_PAGES):
        body = {"filter": {"states": list(engine.STATES)}, "limit": PAGE, "sort_by": "ORDER_CREATION", "sort_dir": "DESC"}
        if last_id:
            body["last_id"] = last_id
        data = await client.request("POST", LIST_PATH, body) or {}
        page = [int(i) for i in data.get("order_ids") or [] if str(i).isdigit()]
        ids.extend(i for i in page if i not in ids)
        previous, last_id = last_id, data.get("last_id") or ""
        if not page or not last_id or last_id == previous:
            break
    return ids


async def fetch_orders(client: OzonClient, ids: list[int]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(ids), GET_CHUNK):
        data = await client.request("POST", GET_PATH, {"order_ids": [str(x) for x in ids[i:i + GET_CHUNK]]}) or {}
        out.extend(o for o in data.get("orders") or [] if isinstance(o, dict))
    return out


async def fetch_bundle(client: OzonClient, bundle_id: str) -> list[dict]:
    items: list[dict] = []
    last_id = None
    for _ in range(MAX_BUNDLE_PAGES):
        body: dict = {"bundle_ids": [bundle_id], "limit": PAGE}
        if last_id:
            body["last_id"] = last_id
        data = await client.request("POST", BUNDLE_PATH, body) or {}
        items.extend(i for i in data.get("items") or [] if isinstance(i, dict))
        last_id = data.get("last_id")
        if not data.get("has_next") or not last_id:
            break
    return items


async def fetch_clusters(client: OzonClient) -> tuple[dict[int, str], dict[int, str], dict[str, int]]:
    """(кластер по macrolocal_cluster_id, кластер по складу, macrolocal_cluster_id по названию кластера)."""
    data = await client.request("POST", CLUSTERS_PATH, {"cluster_type": "CLUSTER_TYPE_OZON"}) or {}
    by_id, by_warehouse, by_name = {}, {}, {}
    for c in data.get("clusters") or []:
        name = (c.get("name") or "").strip()
        macro = engine._int(c.get("macrolocal_cluster_id")) or engine._int(c.get("id"))
        if not name or macro is None:
            continue
        by_id[macro] = name
        by_name[cluster_key(name) or ""] = macro
        for lc in c.get("logistic_clusters") or []:
            for w in (lc or {}).get("warehouses") or []:
                if (wid := engine._int((w or {}).get("warehouse_id"))) is not None:
                    by_warehouse[wid] = name
    return by_id, by_warehouse, by_name


def _settled(o: SupplyOrder) -> bool:
    """Больше не изменится: итоговый статус, состав загружен, акты загружены (если они бывают)."""
    return (o.state in engine.FINAL and o.items_synced_at is not None
            and (o.state not in engine.WITH_ACTS or o.acts_synced_at is not None))


async def _latest_event(db: AsyncSession, order_id: uuid.UUID) -> SupplyOrderEvent | None:
    return (await db.execute(
        select(SupplyOrderEvent).where(SupplyOrderEvent.order_id == order_id)
        .order_by(SupplyOrderEvent.seen_at.desc()).limit(1)
    )).scalar_one_or_none()


async def latest_items(db: AsyncSession, order_id: uuid.UUID, source: str) -> list[SupplyOrderItem]:
    """Последний снимок состава (bundle) или акта (act)."""
    last = await db.scalar(select(func.max(SupplyOrderItem.snapshot_at))
                           .where(SupplyOrderItem.order_id == order_id, SupplyOrderItem.source == source))
    if last is None:
        return []
    return list((await db.execute(
        select(SupplyOrderItem).where(SupplyOrderItem.order_id == order_id, SupplyOrderItem.source == source,
                                      SupplyOrderItem.snapshot_at == last)
    )).scalars())


def _key_of(rows: list[SupplyOrderItem]) -> tuple:
    return engine.content_key([engine.Line(r.sku, r.offer_id, r.name, r.quantity, r.fact, r.approved, r.defect)
                               for r in rows])


async def _save_snapshot(db: AsyncSession, order: SupplyOrder, source: str, lines: list[tuple[int | None, engine.Line]],
                         catalog: dict, now: datetime, stats: SupplySyncStats) -> bool:
    """Новый снимок, если состав изменился; (supply_id, строка)."""
    if engine.content_key([line for _, line in lines]) == _key_of(await latest_items(db, order.id, source)):
        return False
    for supply_id, line in lines:
        product_id = catalog["sku"].get(line.sku) or catalog["offer"].get(line.offer_id)
        if product_id is None:
            stats.unknown_products += 1
        db.add(SupplyOrderItem(
            order_id=order.id, source=source, snapshot_at=now, supply_id=supply_id, sku=line.sku, offer_id=line.offer_id,
            name=line.name, product_id=product_id, quantity=line.quantity, fact=line.fact, approved=line.approved,
            defect=line.defect,
        ))
    return True


async def sync_supply_orders(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> SupplySyncStats:
    stats = SupplySyncStats()
    now = datetime.now(timezone.utc)
    ids = await fetch_ids(client)
    stats.orders = len(ids)
    existing = {o.ozon_order_id: o for o in (await db.execute(
        select(SupplyOrder).where(SupplyOrder.user_id == user_id)
    )).scalars()}
    need = [i for i in ids if i not in existing or not _settled(existing[i])]
    raws = await fetch_orders(client, need)
    stats.fetched = len(raws)
    if not raws:
        return stats

    our_clusters = {w.ozon_warehouse_id: w.cluster_name for w in (await db.execute(
        select(Warehouse).where(Warehouse.user_id == user_id, Warehouse.type == FBO)
    )).scalars() if w.cluster_name}
    by_id: dict[int, str] = {}
    by_warehouse: dict[int, str] = {}
    try:
        by_id, by_warehouse, _ = await fetch_clusters(client)
    except (OzonAuthError, OzonNotConfiguredError):
        raise
    except OzonAPIError as exc:  # без названий кластеров заявки всё равно сохраним
        log.warning("Поставки: список кластеров Ozon не получен: %s", exc.message)
    products = (await db.execute(select(Product).where(Product.user_id == user_id))).scalars().all()
    catalog = {"sku": {p.ozon_sku: p.id for p in products if p.ozon_sku}, "offer": {p.offer_id: p.id for p in products}}

    for raw in raws:
        parsed = engine.parse_order(raw)
        if parsed is None:
            continue
        order = existing.get(parsed.order_id)
        if order is None:
            order = SupplyOrder(user_id=user_id, ozon_order_id=parsed.order_id, first_seen_at=now, state=parsed.state,
                                synced_at=now)
            db.add(order)
            existing[parsed.order_id] = order
            stats.new += 1
        cluster = None
        for s in parsed.supplies:
            cluster = (our_clusters.get(s.storage_warehouse_id) or by_id.get(s.cluster_id)
                       or by_warehouse.get(s.storage_warehouse_id))
            if cluster:
                break
        order.order_number, order.created_at_ozon = parsed.number, parsed.created_at
        order.state, order.state_updated_at = parsed.state, parsed.state_updated_at
        order.timeslot_from, order.timeslot_to, order.timezone = parsed.timeslot_from, parsed.timeslot_to, parsed.timezone
        order.dropoff, order.storage_warehouse = parsed.dropoff, parsed.storage_warehouse
        order.cluster_name = cluster or order.cluster_name
        order.supplies = [{"supply_id": s.supply_id, "bundle_id": s.bundle_id, "storage_warehouse_id": s.storage_warehouse_id,
                           "storage_warehouse": s.storage_warehouse, "cluster_id": s.cluster_id} for s in parsed.supplies]
        order.raw_json, order.synced_at = raw, now
        await db.flush()

        last = await _latest_event(db, order.id)
        current = (parsed.state, parsed.timeslot_from, parsed.timeslot_to)
        if last is None or (last.state, _utc(last.timeslot_from), _utc(last.timeslot_to)) != current:
            db.add(SupplyOrderEvent(order_id=order.id, state=parsed.state, state_updated_at=parsed.state_updated_at,
                                    timeslot_from=parsed.timeslot_from, timeslot_to=parsed.timeslot_to, seen_at=now))
            stats.state_changes += 1

        old = (parsed.state in engine.FINAL and parsed.created_at is not None
               and parsed.created_at < now - timedelta(days=HISTORY_DAYS))
        if old:  # давняя завершённая или отменённая заявка: состав и акты не запрашиваем
            order.items_synced_at = order.items_synced_at or now
            order.acts_synced_at = order.acts_synced_at or now
            continue
        if order.items_synced_at is None or parsed.state not in engine.FINAL:
            lines: list[tuple[int | None, engine.Line]] = []
            for s in parsed.supplies:
                if s.bundle_id:
                    lines.extend((s.supply_id, line) for line in engine.parse_bundle(await fetch_bundle(client, s.bundle_id)))
            if await _save_snapshot(db, order, BUNDLE, lines, catalog, now, stats):
                stats.bundles += 1
            order.items_synced_at = now

        if parsed.state in engine.WITH_ACTS and (order.acts_synced_at is None or parsed.state != engine.COMPLETED):
            lines, failed = [], False
            for s in parsed.supplies:
                if s.supply_id is None:
                    continue
                try:
                    resp = await client.request("POST", ACT_PATH, {"supply_id": s.supply_id}) or {}
                except (OzonAuthError, OzonNotConfiguredError):
                    raise
                except OzonAPIError as exc:
                    log.warning("Поставки: акт поставки %s не получен: %s", s.supply_id, exc.message)
                    failed = True
                    break
                lines.extend((s.supply_id, line) for line in engine.parse_act(resp))
            if failed:
                stats.acts_failed += 1
            else:
                if lines and await _save_snapshot(db, order, ACT, lines, catalog, now, stats):
                    stats.acts += 1
                order.acts_synced_at = now  # актов нет — тоже ответ: у завершённой больше не спрашиваем
    await db.commit()
    return stats


def _utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
