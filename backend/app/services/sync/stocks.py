"""Синхронизация складов и остатков из Ozon в БД.

FBO (склады Ozon) — /v1/analytics/stocks: тот же отчёт, что «FBO → Управление
остатками» в кабинете. Строка = SKU × склад, со складом и кластером. Запрос — по
SKU, не больше 100 за раз; SKU берём из товаров, синхронизированных на шаге 1.2.
   present    = available_stock_count («Доступно к продаже»)
   in_transit = transit_stock_count («В поставках в пути»)
   reserved   — метод не отдаёт (NULL)

FBS/rFBS (склады продавца) — /v2/warehouse/list (пагинация cursor/has_next),
остатки по каждому складу — /v1/product/info/warehouse/stocks (present, reserved).

/v1/warehouse/list отключён Ozon 7.04.2026; /v4/product/info/stocks не делит остаток
по складам — поэтому не используются. См. docs/OZON_API.md.

Снепшот остатка пишется только при изменении. Если пара товар × склад пропала из
ответа, а последний снепшот был ненулевым, — пишем нулевой (товар со склада ушёл).
Всё — одной транзакцией: ошибка посередине ничего не меняет в БД.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.models.product import Product
from app.models.warehouse import FBO, FBS, RFBS, ProductStock, Warehouse
from app.services.sync.parsing import to_count, to_id, to_str

log = logging.getLogger(__name__)

ANALYTICS_STOCKS_PATH = "/v1/analytics/stocks"
WAREHOUSE_LIST_PATH = "/v2/warehouse/list"
WAREHOUSE_STOCKS_PATH = "/v1/product/info/warehouse/stocks"

SKU_BATCH = 100  # максимум skus в /v1/analytics/stocks
WAREHOUSE_PAGE = 200  # максимум limit в /v2/warehouse/list
STOCKS_PAGE = 1000  # максимум limit в /v1/product/info/warehouse/stocks
MAX_PAGES = 1000
ACTIVE_FBS_STATUS = "created"  # «Активный» в кабинете

Reading = tuple[int, int | None, int | None]  # present, reserved, in_transit


@dataclass
class StockSyncStats:
    warehouses_created: int = 0
    warehouses_updated: int = 0
    stocks_changed: int = 0  # сохранено новых снепшотов (включая обнуления)
    stocks_unchanged: int = 0
    unknown_products: int = 0  # строки Ozon по товарам, которых ещё нет в БД (нужна синхронизация товаров)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class _WarehouseInfo:
    name: str
    type: str
    is_active: bool
    cluster_id: int | None = None
    cluster_name: str | None = None


def _has_next(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


# ── запросы к Ozon ──
async def fetch_fbo(client: OzonClient, skus: list[int]) -> list[dict]:
    rows: list[dict] = []
    for start in range(0, len(skus), SKU_BATCH):
        batch = [str(s) for s in skus[start:start + SKU_BATCH]]
        data = await client.request("POST", ANALYTICS_STOCKS_PATH, {"skus": batch})
        items = data.get("items") if isinstance(data, dict) else None
        rows.extend(i for i in items or [] if isinstance(i, dict))
    return rows


async def fetch_fbs_warehouses(client: OzonClient) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        body: dict[str, Any] = {"limit": WAREHOUSE_PAGE}
        if cursor:
            body["cursor"] = cursor
        data = await client.request("POST", WAREHOUSE_LIST_PATH, body)
        page = data.get("warehouses") if isinstance(data, dict) else None
        out.extend(w for w in page or [] if isinstance(w, dict))
        cursor = (data.get("cursor") or "") if isinstance(data, dict) else ""
        if not page or not cursor or not _has_next(data.get("has_next")):
            break
    return out


async def fetch_fbs_stocks(client: OzonClient, ozon_warehouse_id: int) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        data = await client.request(
            "POST", WAREHOUSE_STOCKS_PATH, {"cursor": cursor, "limit": STOCKS_PAGE, "warehouse_id": ozon_warehouse_id}
        )
        page = data.get("stocks") if isinstance(data, dict) else None
        out.extend(s for s in page or [] if isinstance(s, dict))
        cursor = (data.get("cursor") or "") if isinstance(data, dict) else ""
        if not page or not cursor or not _has_next(data.get("has_next")):
            break
    return out


# ── БД ──
async def _latest_stocks(db: AsyncSession, warehouse_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, uuid.UUID], ProductStock]:
    if not warehouse_ids:
        return {}
    latest = (
        select(ProductStock.product_id, ProductStock.warehouse_id, func.max(ProductStock.snapshot_at).label("mx"))
        .where(ProductStock.warehouse_id.in_(warehouse_ids))
        .group_by(ProductStock.product_id, ProductStock.warehouse_id)
        .subquery()
    )
    res = await db.execute(
        select(ProductStock).join(
            latest,
            and_(
                ProductStock.product_id == latest.c.product_id,
                ProductStock.warehouse_id == latest.c.warehouse_id,
                ProductStock.snapshot_at == latest.c.mx,
            ),
        )
    )
    return {(s.product_id, s.warehouse_id): s for s in res.scalars()}


def _reading(s: ProductStock) -> Reading:
    return (s.present, s.reserved, s.in_transit)


def _is_empty(r: Reading) -> bool:
    return not r[0] and not r[1] and not r[2]


async def sync_stocks(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> StockSyncStats:
    stats = StockSyncStats()
    now = datetime.now(timezone.utc)

    products = list((await db.execute(select(Product).where(Product.user_id == user_id))).scalars())
    by_sku = {p.ozon_sku: p for p in products if p.ozon_sku}
    by_pid = {p.ozon_product_id: p for p in products}
    by_offer = {p.offer_id: p for p in products}

    infos: dict[int, _WarehouseInfo] = {}
    readings: dict[tuple[uuid.UUID, int], Reading] = {}

    # FBO: склады Ozon с кластерами
    for row in await fetch_fbo(client, sorted(by_sku)):
        wid = to_id(row.get("warehouse_id"))
        if not wid:
            continue
        product = by_sku.get(to_id(row.get("sku"))) or by_offer.get(row.get("offer_id"))
        if product is None:
            stats.unknown_products += 1
            continue
        infos.setdefault(wid, _WarehouseInfo(
            name=to_str(row.get("warehouse_name"), 255) or str(wid), type=FBO, is_active=True,
            cluster_id=to_id(row.get("cluster_id")), cluster_name=to_str(row.get("cluster_name"), 255),
        ))
        present, _, transit = readings.get((product.id, wid), (0, None, 0))
        # одна пара может прийти несколькими строками (теги, зоны) — складываем
        readings[(product.id, wid)] = (
            present + to_count(row.get("available_stock_count")), None, (transit or 0) + to_count(row.get("transit_stock_count")),
        )

    # FBS / rFBS: склады продавца
    for w in await fetch_fbs_warehouses(client):
        wid = to_id(w.get("warehouse_id"))
        if not wid:
            continue
        active = w.get("status") == ACTIVE_FBS_STATUS
        infos[wid] = _WarehouseInfo(
            name=to_str(w.get("name"), 255) or str(wid), type=RFBS if w.get("is_rfbs") else FBS, is_active=active,
        )
        for s in await fetch_fbs_stocks(client, wid):
            product = (
                by_pid.get(to_id(s.get("product_id"))) or by_sku.get(to_id(s.get("sku"))) or by_offer.get(s.get("offer_id"))
            )
            if product is None:
                stats.unknown_products += 1
                continue
            readings[(product.id, wid)] = (to_count(s.get("present")), to_count(s.get("reserved")), None)

    # склады: upsert; не пришедшие в этот раз — неактивны
    res = await db.execute(select(Warehouse).where(Warehouse.user_id == user_id))
    warehouses = {w.ozon_warehouse_id: w for w in res.scalars()}
    for wid, info in infos.items():
        w = warehouses.get(wid)
        if w is None:
            w = Warehouse(user_id=user_id, ozon_warehouse_id=wid, synced_at=now, **asdict(info))
            db.add(w)
            warehouses[wid] = w
            stats.warehouses_created += 1
            continue
        changed = False
        for field, value in asdict(info).items():
            if getattr(w, field) != value:
                setattr(w, field, value)
                changed = True
        w.synced_at = now
        stats.warehouses_updated += changed
    for wid, w in warehouses.items():
        if wid not in infos and w.is_active:
            w.is_active = False
            stats.warehouses_updated += 1
    await db.flush()

    # остатки: снепшот только при изменении
    by_ozon_wid = {wid: w.id for wid, w in warehouses.items()}
    current = {(pid, by_ozon_wid[wid]): r for (pid, wid), r in readings.items()}
    latest = await _latest_stocks(db, list(by_ozon_wid.values()))

    for (product_id, warehouse_id), reading in current.items():
        last = latest.get((product_id, warehouse_id))
        if last is not None and _reading(last) == reading:
            stats.stocks_unchanged += 1
            continue
        db.add(ProductStock(product_id=product_id, warehouse_id=warehouse_id, snapshot_at=now,
                            present=reading[0], reserved=reading[1], in_transit=reading[2]))
        stats.stocks_changed += 1

    for key, last in latest.items():
        if key not in current and not _is_empty(_reading(last)):
            zero = (0, 0 if last.reserved is not None else None, 0 if last.in_transit is not None else None)
            db.add(ProductStock(product_id=key[0], warehouse_id=key[1], snapshot_at=now,
                                present=zero[0], reserved=zero[1], in_transit=zero[2]))
            stats.stocks_changed += 1

    await db.commit()
    log.info("Синхронизация остатков: пользователь %s → %s", user_id, stats.as_dict())
    return stats
