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
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.models.cluster_metrics import OzonSupplyRec, OzonSupplyRecSync
from app.models.product import Product
from app.services.sync.finance import today_msk
from app.services.sync.parsing import to_count, to_decimal, to_id, to_str
from app.services.user_settings import get_settings, supply_ozon_days

log = logging.getLogger(__name__)

LOCAL_SALE_PATH = "/v1/analytics/local-sale/items-clusters/info"
PAGE = 1000  # максимум limit
MAX_PAGES = 50
SKU_BATCH = 100  # SKU в одном запросе: фильтр `skus` обязателен, предел Ozon не назвал
PERIOD_DAYS = 28  # за сколько дней Ozon смотрит продажи — как ads в «Управлении остатками»
SUPPLY_PERIODS = {7: "ONE_WEEK", 14: "TWO_WEEKS", 28: "FOUR_WEEKS", 56: "EIGHT_WEEKS"}  # дней → `supply_period`
DEFAULT_SUPPLY_DAYS = 28

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


async def fetch(client: OzonClient, today, skus: list[int], days: int = DEFAULT_SUPPLY_DAYS) -> list[dict]:
    """Рекомендации по товарам `skus` (пачками по SKU_BATCH) на `days` дней; товаров нет — запроса нет."""
    period = {"from": (today - timedelta(days=PERIOD_DAYS)).isoformat(), "to": (today - timedelta(days=1)).isoformat()}
    out: list[dict] = []
    for start in range(0, len(skus), SKU_BATCH):
        batch = [str(s) for s in skus[start:start + SKU_BATCH]]
        offset = 0
        for _ in range(MAX_PAGES):
            body = {
                "filter": {"delivery_schema": "ALL", "period": period, "skus": batch,
                           "supply_period": SUPPLY_PERIODS[days]},
                "limit": PAGE, "offset": offset, "sort_by": "IMPACT_SHARE", "sort_dir": "DESC",
            }
            data = await client.request("POST", LOCAL_SALE_PATH, body) or {}
            page = [i for i in data.get("items") or [] if isinstance(i, dict)]
            out.extend(page)
            offset += len(page)
            total = to_count(data.get("total"))
            if len(page) < PAGE or (total and offset >= total):
                break
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
    rows = await fetch(client, today_msk(), skus, days)
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
    log.info("Рекомендации Ozon по поставкам: пользователь %s → строк %d, изменилось %d", user_id, len(current), changed)
    return {"ozon_recs": len(current), "ozon_recs_changed": changed,
            "ozon_recs_positive": sum(1 for r in current.values() if r[0] > 0)}
