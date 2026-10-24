"""Заявки на поставку и их результаты (шаг 10.18): список со статусами и датами, история статусов, что привезли против
плана, как продавалось после приёмки; Excel.

- Что привезли: в плане (если заявка из плана сервиса или к нему привязана) → заявлено в заявке (последний снимок
  состава) → принято по актам (engine/supply_orders). Актов ещё нет — «нет данных», а не 0.
- Как продавалось после: продажи товара (в целом — склада в начислениях нет, `sales_daily`) с дня приёмки до сегодня, но
  не больше 30 дней, против такого же числа дней до приёмки; дни без начислений — пропуск, сравнение по средним за день
  (как результаты акций, engine/promo_results). «Хватит на» — принято / продажи в день после.
"""
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import promo_results
from app.engine import supply_orders as engine
from app.models.product import Product
from app.models.supply import ACT, SupplyOrder, SupplyOrderEvent, SupplyPlan
from app.schemas.sync import utc
from app.services import sales as sales_svc
from app.services.supply_plans import PlanError, _local_day, _slot_time, accepted_days, current_items, latest_bundle_qty
from app.services.sync.finance import today_msk
from app.services.sync.supply_orders import latest_items
from app.services.xlsx_export import Col, table_xlsx

AFTER_DAYS = 30


async def _accepted_qty(db: AsyncSession, order: SupplyOrder) -> dict | None:
    rows = await latest_items(db, order.id, ACT)
    if not rows:
        return None
    out: dict = {}
    for r in rows:
        key = r.product_id or (r.sku, r.offer_id)
        out[key] = out.get(key, 0) + (r.fact if r.fact is not None else r.approved or 0)
    return out


def _order_out(o: SupplyOrder, declared: dict | None, accepted: dict | None, accepted_on: date | None,
               plans: dict[uuid.UUID, SupplyPlan]) -> dict:
    plan = plans.get(o.plan_id) if o.plan_id else None
    return {
        "id": o.id, "ozon_order_id": o.ozon_order_id, "number": o.order_number, "created_at": utc(o.created_at_ozon),
        "state": o.state, "state_text": engine.state_text(o.state), "group": engine.group_of(o.state),
        "state_updated_at": utc(o.state_updated_at), "timeslot_from": utc(o.timeslot_from),
        "timeslot_to": utc(o.timeslot_to), "timeslot_day": _local_day(o.timeslot_from, o.timezone),
        "timeslot_time": _slot_time(o), "dropoff": o.dropoff, "storage_warehouse": o.storage_warehouse,
        "cluster": o.cluster_name, "accepted_on": accepted_on,
        "products": len(declared) if declared is not None else None,
        "declared": sum(declared.values()) if declared is not None else None,
        "accepted": sum(accepted.values()) if accepted is not None else None,
        "plan": {"id": plan.id, "destination_name": plan.destination_name} if plan else None,
        "synced_at": utc(o.synced_at),
    }


async def list_orders(db: AsyncSession, user_id: uuid.UUID, group: str | None = None, search: str | None = None) -> dict:
    orders = (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == user_id))).scalars().all()
    ids = [o.id for o in orders]
    declared = await latest_bundle_qty(db, ids)
    accepted = {o.id: await _accepted_qty(db, o) for o in orders if o.acts_synced_at is not None}
    days = await accepted_days(db, ids)
    plans = {p.id: p for p in (await db.execute(select(SupplyPlan).where(SupplyPlan.user_id == user_id))).scalars()}
    counts = {g: 0 for g in engine.GROUPS}
    for o in orders:
        counts[engine.group_of(o.state)] += 1
    offers: dict[uuid.UUID, set[str]] = {}
    if search:
        offers = await _offers_by_order(db, ids, declared)
    q = (search or "").strip().casefold()
    out = []
    for o in orders:
        if group and engine.group_of(o.state) != group:
            continue
        if q and not (q in (o.order_number or "").casefold() or q in str(o.ozon_order_id)
                      or any(q in offer.casefold() for offer in offers.get(o.id, ()))
                      or q in (o.cluster_name or "").casefold() or q in (o.storage_warehouse or "").casefold()):
            continue
        out.append(_order_out(o, declared.get(o.id), accepted.get(o.id), days.get(o.id), plans))
    out.sort(key=lambda r: (r["created_at"] is None, -(r["created_at"].timestamp() if r["created_at"] else 0)))
    return {"counts": counts, "total": len(orders), "items": out, "group_text": engine.GROUP_TEXT}


async def _offers_by_order(db: AsyncSession, ids: list[uuid.UUID], declared: dict) -> dict[uuid.UUID, set[str]]:
    product_ids = {k for d in declared.values() for k in d if isinstance(k, uuid.UUID)}
    names = {p.id: p.offer_id for p in (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars()} \
        if product_ids else {}
    return {oid: {names.get(k, "") if isinstance(k, uuid.UUID) else (k[1] or "") for k in d}
            for oid, d in declared.items()}


async def _sales_after(db: AsyncSession, user_id: uuid.UUID, offer_id: str, arrived: date, today: date) -> dict:
    """Продажи товара после приёмки против такого же периода до (дни без начислений — пропуск)."""
    last = min(today - timedelta(days=1), arrived + timedelta(days=AFTER_DAYS - 1))
    if last < arrived:
        return {"known": False, "after_days": 0}
    (a1, a2), (b1, b2) = promo_results.windows(arrived, last)
    days = {}
    data = await sales_svc.daily(db, user_id, b1, a2, offer_id)
    for d in data["days"]:
        if d["qty_sold"] is not None:
            days[date.fromisoformat(d["date"])] = promo_results.Day(
                d["qty_sold"] - (d["qty_returned"] or 0), Decimal(d["revenue"]), None)
    after, before = promo_results.totals(days, a1, a2), promo_results.totals(days, b1, b2)
    c = promo_results.compare(after, before)
    per_day = (Decimal(after.qty) / after.days_with_data).quantize(Decimal("0.01"), ROUND_HALF_UP) \
        if after.days_with_data else None
    return {
        "known": bool(after.days_with_data), "after_days": after.days, "after_days_with_data": after.days_with_data,
        "after_qty": after.qty if after.days_with_data else None, "per_day_after": per_day,
        "before_qty": c["base"].qty if c["known"] else None, "change_pct": c["qty_change_pct"],
    }


async def order_detail(db: AsyncSession, user_id: uuid.UUID, order_id: uuid.UUID) -> dict:
    order = await db.get(SupplyOrder, order_id)
    if order is None or order.user_id != user_id:
        raise PlanError("Заявка на поставку не найдена", 404)
    declared = (await latest_bundle_qty(db, [order.id])).get(order.id)
    accepted = await _accepted_qty(db, order) if order.acts_synced_at is not None else None
    arrived = (await accepted_days(db, [order.id])).get(order.id)
    plan = await db.get(SupplyPlan, order.plan_id) if order.plan_id else None
    planned = {}
    if plan is not None:
        planned = {i.product_id: i.qty for i in (await current_items(db, [plan.id]))[plan.id] if i.qty > 0}
    out = _order_out(order, declared, accepted, arrived, {plan.id: plan} if plan else {})
    events = (await db.execute(select(SupplyOrderEvent).where(SupplyOrderEvent.order_id == order.id)
                               .order_by(SupplyOrderEvent.seen_at))).scalars().all()
    out["events"] = [{"state": e.state, "state_text": engine.state_text(e.state),
                      "at": utc(e.state_updated_at or e.seen_at), "timeslot_from": utc(e.timeslot_from)} for e in events]

    rows = engine.results(planned, declared, accepted)
    defects = {r.product_id or (r.sku, r.offer_id) for r in await latest_items(db, order.id, ACT) if r.defect}
    keys = [r.key for r in rows if isinstance(r.key, uuid.UUID)]
    products = {p.id: p for p in (await db.execute(select(Product).where(Product.id.in_(keys)))).scalars()} if keys else {}
    today = today_msk()
    items = []
    for r in rows:
        p = products.get(r.key) if isinstance(r.key, uuid.UUID) else None
        product = {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku} if p else \
            {"id": None, "offer_id": r.key[1] or "—", "name": None, "ozon_sku": r.key[0]}
        after = await _sales_after(db, user_id, p.offer_id, arrived, today) if p and arrived else None
        accepted_n = r.accepted if r.accepted is not None else r.declared
        lasts = None
        if after and after.get("per_day_after") and accepted_n:
            lasts = int(Decimal(accepted_n) / after["per_day_after"])
        items.append({"product": product, "planned": r.planned, "declared": r.declared, "accepted": r.accepted,
                      "diff": r.diff, "plan_diff": r.plan_diff, "defect": r.key in defects, "sales_after": after,
                      "lasts_days": lasts})
    out["items"] = sorted(items, key=lambda i: (i["diff"] is None or i["diff"] >= 0, i["product"]["offer_id"]))
    out["totals"] = engine.totals(rows)
    out["has_acts"] = accepted is not None
    return out


# ── Excel истории ──
COLUMNS = [
    Col("Номер заявки", 16), Col("Создана", 12, "date"), Col("Статус", 22), Col("Кластер", 26), Col("Склад", 26),
    Col("Таймслот", 12, "date"), Col("Принята", 12, "date"), Col("Заявлено, шт.", 11, "int"),
    Col("Принято, шт.", 11, "int"), Col("Разница, шт.", 11, "int"), Col("План сервиса", 26),
]


def to_xlsx(data: dict) -> bytes:
    rows = []
    for o in data["items"]:
        diff = o["accepted"] - o["declared"] if o["accepted"] is not None and o["declared"] is not None else None
        rows.append([
            o["number"] or str(o["ozon_order_id"]), o["created_at"].date() if o["created_at"] else None, o["state_text"],
            o["cluster"], o["storage_warehouse"], o["timeslot_day"], o["accepted_on"], o["declared"],
            o["accepted"] if o["accepted"] is not None else "нет актов", diff,
            o["plan"]["destination_name"] if o["plan"] else None,
        ])
    notes = [
        f"Заявки на поставку FBO — {today_msk():%d.%m.%Y}",
        "",
        "Статусы и состав — из Ozon (синхронизация раз в 3 часа). Принято — по актам приёмки; актов ещё нет — «нет актов».",
        "Разница = принято − заявлено: меньше нуля — недовоз (или брак), больше — излишек.",
    ]
    return table_xlsx("Заявки", COLUMNS, rows, notes_title="Пояснения", notes=notes)
