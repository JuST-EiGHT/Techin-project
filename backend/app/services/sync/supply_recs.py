"""Рекомендованная поставка Ozon по товару и кластеру (шаг 10.18a) — «Аналитика → Планирование поставок →
Локальность продаж», `/v1/analytics/local-sale/items-clusters/info` (бета).

Строка ответа — SKU × кластер покупателя (`macrolocal_cluster_to_id`): `metrics.recommended_supply` — сколько Ozon
советует поставить, `attention_level`, причины переплаты за логистику (`overpayment_reasons`, например
`NO_SUPPLIES_TO_CLUSTER` — в кластер не возят). Решение владельца (10.11.2026): это нижняя планка поставки — то, что
«прямо необходимо»; сервис везёт не меньше.

Запрос (схема Ozon — OpenAPI `analytics.v1.AnalyticsLocalSaleItemsClustersInfoRequest`; в выгрузке документации
вложенные поля не раскрыты): `filter{delivery_schema: ALL, period{from, to} — YYYY-MM-DD, skus — обязательно (строками),
supply_period}`, по 1000 строк, `offset` до `total`. Без `skus` Ozon отвечает 400 — так было до правки 16.11: метод «товара
по кластерам» требует список SKU, шлём товары магазина пачками по 100. `supply_period` — «Частота поставки» в
«Настроить рекомендации» кабинета: ONE_WEEK / TWO_WEEKS / FOUR_WEEKS / EIGHT_WEEKS; Ozon советует столько, чтобы хватило
до следующей поставки, — настройка пользователя (`supply_ozon_days`, по умолчанию 28 дней, как у владельца в кабинете:
«Рекомендуемая поставка на FBO — на 28 дней»). Загружается вместе с заявками на поставку
(вид «Заявки на поставку», раз в 3 часа); сбой метода не мешает заявкам — пишется в `ozon_supply_rec_syncs`, а расчёт
идёт по продажам. Снимок — только при изменении, пара пропала из ответа — нулевая запись.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import (
    OzonAPIError,
    OzonAuthError,
    OzonNotConfiguredError,
)
from app.models.cluster_metrics import OzonSupplyRec, OzonSupplyRecSync
from app.models.product import Product
from app.models.warehouse import FBO as WH_FBO
from app.models.warehouse import Warehouse
from app.services.sync.finance import today_msk
from app.services.sync.parsing import to_count, to_decimal, to_id, to_str
from app.services.user_settings import get_settings, supply_ozon_days

log = logging.getLogger(__name__)

LOCAL_SALE_PATH = "/v1/analytics/local-sale/items-clusters/info"
# правка 19.11: «товар → кластеры» на реальном ключе отдаёт строки, но везде recommended_supply = 0 (FBO и ALL одинаково),
# а кабинет советует 8, 8, 4, 2 шт. Тогда спрашиваем соседний метод «кластер → товары» (вид «По кластерам» в кабинете):
# фильтр `macrolocal_cluster_to_ids` обязателен, `skus` — нет; ответ `items[]{cluster_to_id, item{sku}, metrics}`.
CLUSTERS_PATH = "/v1/analytics/local-sale/clusters-items/info"
CLUSTER_BATCH = 50  # кластеров в одном запросе
ITEMS, CLUSTERS = "items", "clusters"  # каким методом получены рекомендации
PAGE = 1000  # максимум limit
MAX_PAGES = 50
SKU_BATCH = 100  # SKU в одном запросе: фильтр `skus` обязателен, предел Ozon не назвал
PERIOD_DAYS = 28  # за сколько дней Ozon смотрит продажи — как ads в «Управлении остатками»
SUPPLY_PERIODS = {7: "ONE_WEEK", 14: "TWO_WEEKS", 28: "FOUR_WEEKS", 56: "EIGHT_WEEKS"}  # дней → `supply_period`
DEFAULT_SUPPLY_DAYS = 28
# правка 18.11: рекомендация поставки у Ozon — для FBO («Рекомендуемая поставка на FBO» в кабинете); с `ALL` Ozon на
# реальном ключе прислал нули по всем товарам, хотя в кабинете советовал 8, 8, 4, 2 шт.
DELIVERY_SCHEMA = "FBO"

Rec = tuple  # (recommended, attention_level, reasons)
_CENT = Decimal("0.01")


def parse(item: dict) -> tuple[int, int, Rec] | None:
    """(sku, кластер покупателя, (рекомендовано, внимание, причины)); без SKU или кластера — None."""
    sku, cluster = to_id(item.get("sku")), to_id(item.get("macrolocal_cluster_to_id"))
    if sku is None or cluster is None:
        return None
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    reasons = [
        {"reason": to_str(r.get("reason"), 64), "quantity": to_count(r.get("quantity")),
         "amount": None if (a := to_decimal(r.get("amount"), _CENT)) is None else str(a)}
        for r in metrics.get("overpayment_reasons") or [] if isinstance(r, dict) and r.get("reason")
    ]
    return sku, cluster, (to_count(metrics.get("recommended_supply")), to_str(metrics.get("attention_level"), 16),
                          reasons or None)


def _period(today, to_today: bool = False) -> dict:
    end = today if to_today else today - timedelta(days=1)
    return {"from": (end - timedelta(days=PERIOD_DAYS - 1)).isoformat(), "to": end.isoformat()}


async def _pages(client: OzonClient, path: str, flt: dict) -> list[dict]:
    out: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        body = {"filter": flt, "limit": PAGE, "offset": offset, "sort_by": "IMPACT_SHARE", "sort_dir": "DESC"}
        data = await client.request("POST", path, body) or {}
        page = [i for i in data.get("items") or [] if isinstance(i, dict)]
        out.extend(page)
        offset += len(page)
        total = to_count(data.get("total"))
        if len(page) < PAGE or (total and offset >= total):
            break
    return out


def _filter(today, days: int | None, schema: str, to_today: bool) -> dict:
    flt: dict = {"delivery_schema": schema, "period": _period(today, to_today)}
    if days is not None:
        flt["supply_period"] = SUPPLY_PERIODS[days]
    return flt


async def fetch(client: OzonClient, today, skus: list[int], days: int | None = DEFAULT_SUPPLY_DAYS,
                schema: str = DELIVERY_SCHEMA, *, to_today: bool = False) -> list[dict]:
    """«Товар → кластеры»: рекомендации по товарам `skus` (пачками по SKU_BATCH); товаров нет — запроса нет.
    `days=None` — без `supply_period` (для проверки администратора)."""
    out: list[dict] = []
    for start in range(0, len(skus), SKU_BATCH):
        flt = _filter(today, days, schema, to_today) | {"skus": [str(s) for s in skus[start:start + SKU_BATCH]]}
        out.extend(await _pages(client, LOCAL_SALE_PATH, flt))
    return out


async def fetch_by_clusters(client: OzonClient, today, clusters: list[int], days: int | None = DEFAULT_SUPPLY_DAYS,
                            schema: str = DELIVERY_SCHEMA, *, skus: list[int] | None = None) -> list[dict]:
    """«Кластер → товары» по кластерам доставки `clusters` (пачками по CLUSTER_BATCH); строки — в том же виде, что у
    «товар → кластеры» (`sku`, `macrolocal_cluster_to_id`, `metrics`), чтобы разбирать одним `parse`."""
    out: list[dict] = []
    for start in range(0, len(clusters), CLUSTER_BATCH):
        flt = _filter(today, days, schema, False) | {
            "macrolocal_cluster_to_ids": [str(c) for c in clusters[start:start + CLUSTER_BATCH]]}
        if skus:
            flt["skus"] = [str(s) for s in skus]
        for row in await _pages(client, CLUSTERS_PATH, flt):
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            out.append({"sku": item.get("sku", row.get("sku")), "offer_id": item.get("offer_id"),
                        "macrolocal_cluster_to_id": row.get("cluster_to_id", row.get("macrolocal_cluster_to_id")),
                        "metrics": row.get("metrics"), "raw": row})  # raw — для проверки администратора
    return out


def _has_positive(rows: list[dict]) -> bool:
    return any((p := parse(r)) is not None and p[2][0] > 0 for r in rows)


PROBE_PAUSE = 1.1  # с между вариантами проверки: у метода лимит запросов в секунду (429 на реальном ключе)


async def probe(client: OzonClient, today, skus: list[int], days: int, clusters: list[int]) -> dict[str, dict]:
    """Проверка для администратора (`admin.sh supply-recs-check`): одни и те же товары — разными запросами («товар →
    кластеры» по FBO и ALL, с концом периода вчера и сегодня, без `supply_period`; «кластер → товары» по кластерам
    `clusters`). У каждого: строк, с рекомендацией > 0, сумма, кластеры, первые рекомендации, поля `metrics` и первая
    строка ответа как есть. Только чтение, без записи в базу."""
    variants = [
        (f"товар → кластеры, {DELIVERY_SCHEMA}, {days} дн.", lambda: fetch(client, today, skus, days)),
        ("товар → кластеры, ALL", lambda: fetch(client, today, skus, days, "ALL")),
        ("товар → кластеры, период до сегодня", lambda: fetch(client, today, skus, days, to_today=True)),
        ("товар → кластеры, без срока поставки", lambda: fetch(client, today, skus, None)),
        (f"кластер → товары, {DELIVERY_SCHEMA}, {days} дн.",
         lambda: fetch_by_clusters(client, today, clusters, days, skus=skus)),
    ]
    out: dict[str, dict] = {}
    for i, (title, call) in enumerate(variants):
        if i:
            await asyncio.sleep(PROBE_PAUSE)
        try:
            raw = await call()
        except Exception as e:  # noqa: BLE001 — для отчёта: какой запрос не прошёл и почему
            out[title] = {"error": getattr(e, "message", str(e))[:300]}
            continue
        rows = [p for p in (parse(r) for r in raw) if p]
        positive = [(sku, cluster, rec[0]) for sku, cluster, rec in rows if rec[0] > 0]
        keys = sorted({k for r in raw if isinstance(r.get("metrics"), dict) for k in r["metrics"]})
        out[title] = {"rows": len(rows), "positive": len(positive), "total": sum(q for *_, q in positive),
                      "clusters": sorted({c for _, c, _ in rows}), "top": sorted(positive, key=lambda x: -x[2])[:10],
                      "metric_keys": keys, "sample": json.dumps(raw[0].get("raw", raw[0]), ensure_ascii=False)[:600] if raw else None}
    return out


async def latest_recs(db: AsyncSession, user_id: uuid.UUID) -> dict[tuple[int, int], OzonSupplyRec]:
    latest = (
        select(OzonSupplyRec.sku, OzonSupplyRec.macrolocal_cluster_id, func.max(OzonSupplyRec.snapshot_at).label("mx"))
        .where(OzonSupplyRec.user_id == user_id)
        .group_by(OzonSupplyRec.sku, OzonSupplyRec.macrolocal_cluster_id)
        .subquery()
    )
    res = await db.execute(select(OzonSupplyRec).join(latest, and_(
        OzonSupplyRec.sku == latest.c.sku, OzonSupplyRec.macrolocal_cluster_id == latest.c.macrolocal_cluster_id,
        OzonSupplyRec.snapshot_at == latest.c.mx,
    )).where(OzonSupplyRec.user_id == user_id))
    return {(r.sku, r.macrolocal_cluster_id): r for r in res.scalars()}


def _rec_of(r: OzonSupplyRec) -> Rec:
    return (r.recommended, r.attention_level, r.reasons or None, r.period_days)


async def _state(db: AsyncSession, user_id: uuid.UUID) -> OzonSupplyRecSync:
    state = await db.get(OzonSupplyRecSync, user_id)
    if state is None:
        state = OzonSupplyRecSync(user_id=user_id, attempted_at=datetime.now(timezone.utc), ok=False, items=0)
        db.add(state)
    return state


async def record_failure(db: AsyncSession, user_id: uuid.UUID, error: str) -> None:
    state = await _state(db, user_id)
    state.attempted_at, state.ok, state.error = datetime.now(timezone.utc), False, error[:500]
    await db.commit()


async def sync_supply_recs(db: AsyncSession, user_id: uuid.UUID, client: OzonClient, names: dict[int, str]) -> dict:
    """`names` — кластер по macrolocal_cluster_id из /v1/cluster/list (может быть пустым)."""
    now = datetime.now(timezone.utc)
    days = supply_ozon_days(await get_settings(db, user_id))
    catalog = list((await db.execute(select(Product).where(Product.user_id == user_id))).scalars())
    products = {p.ozon_sku: p.id for p in catalog if p.ozon_sku}
    skus = sorted(p.ozon_sku for p in catalog if p.ozon_sku and not p.is_archived)
    today = today_msk()
    rows = await fetch(client, today, skus, days)
    source = ITEMS
    ours = {w for w in (await db.execute(select(Warehouse.macrolocal_cluster_id).where(
        Warehouse.user_id == user_id, Warehouse.type == WH_FBO))).scalars() if w}
    clusters = sorted(set(names) | ours)
    second: dict = {}  # правка 20.11: что дал второй метод — в статистике загрузки (журнал на сервере его не показывает)
    if clusters and not _has_positive(rows):  # правка 19.11: везде нули — спрашиваем «кластер → товары»
        try:
            wanted = set(skus)
            raw = await fetch_by_clusters(client, today, clusters, days)
            alt = [r for r in raw if to_id(r.get("sku")) in wanted]
            second = {"ozon_recs_clusters_asked": len(clusters), "ozon_recs_clusters_rows": len(raw),
                      "ozon_recs_clusters_ours": len(alt),
                      "ozon_recs_clusters_positive": sum(1 for r in alt if (p := parse(r)) and p[2][0] > 0)}
        except (OzonAuthError, OzonNotConfiguredError):
            raise
        except OzonAPIError as e:
            log.warning("Рекомендации Ozon «кластер → товары» не получены: %s (%s)", e.message, e.ozon_message)
            alt = []
            second = {"ozon_recs_clusters_error": f"{e.message}: {e.ozon_message or ''}"[:300]}
        if _has_positive(alt):
            rows, source = alt, CLUSTERS
    current: dict[tuple[int, int], Rec] = {}
    for row in rows:
        if (parsed := parse(row)) is not None:
            sku, cluster, rec = parsed
            current[(sku, cluster)] = (*rec, days)
    last = await latest_recs(db, user_id)
    changed = 0
    for (sku, cluster), rec in current.items():
        if (prev := last.get((sku, cluster))) is not None and _rec_of(prev) == rec:
            continue
        db.add(OzonSupplyRec(user_id=user_id, product_id=products.get(sku), sku=sku, macrolocal_cluster_id=cluster,
                             cluster_name=names.get(cluster), recommended=rec[0], attention_level=rec[1],
                             reasons=rec[2], period_days=days, snapshot_at=now))
        changed += 1
    for (sku, cluster), prev in last.items():
        if (sku, cluster) not in current and prev.recommended > 0:  # Ozon больше не советует — нулевая запись
            db.add(OzonSupplyRec(user_id=user_id, product_id=prev.product_id, sku=sku, macrolocal_cluster_id=cluster,
                                 cluster_name=prev.cluster_name, recommended=0, period_days=days, snapshot_at=now))
            changed += 1
    state = await _state(db, user_id)
    state.attempted_at = state.synced_at = now
    state.ok, state.error, state.items, state.period_days = True, None, len(current), days
    await db.commit()
    log.info("Рекомендации Ozon по поставкам: пользователь %s → строк %d, изменилось %d, метод %s", user_id,
             len(current), changed, source)
    return {"ozon_recs": len(current), "ozon_recs_changed": changed,
            "ozon_recs_positive": sum(1 for r in current.values() if r[0] > 0), "ozon_recs_source": source, **second}
