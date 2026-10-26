"""Планы поставок (шаг 10.18): что и сколько везти в кластер Ozon (или закупить на свой склад) — из рекомендаций 5.2 с
ручной корректировкой; дата для календаря; привязка к заявке Ozon; Excel.

- Строки плана — с версиями: корректировка — новая строка, прежняя получает `replaced_at` (правило 12); действует строка
  без `replaced_at`. Количество 0 — товар исключён из плана (строка остаётся в истории).
- Менять можно план в статусе «черновик»; заявка создана или привязана — план фиксирован, правда — заявка Ozon.
- Календарь: даты планов, таймслоты заявок Ozon и дни, когда поставку приняли.
"""
import hashlib
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import supply_orders as orders_engine
from app.engine import supply_planner as engine
from app.models.product import Product
from app.models.supply import (
    BUNDLE,
    PLAN_CANCELLED,
    PLAN_DRAFT,
    PLAN_ORDERED,
    SupplyOrder,
    SupplyOrderEvent,
    SupplyOrderItem,
    SupplyPlan,
    SupplyPlanItem,
)
from app.schemas.sync import utc
from app.services.supply import KIND_TEXT, cluster_key
from app.services.sync.finance import MSK
from app.services.xlsx_export import Col, table_xlsx

MAX_QTY = 100_000
MAX_ITEMS = 500
NOTE_MAX = 1000
CALENDAR_MAX_DAYS = 120


class PlanError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message, self.status_code = message, status_code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(tz: str | None):
    """Часовой пояс склада из ответа Ozon; нет или неизвестный — Москва."""
    try:
        return ZoneInfo(tz) if tz else MSK
    except Exception:  # noqa: BLE001
        return MSK


def _local_day(dt: datetime | None, tz: str | None = None) -> date | None:
    """День по часовому поясу склада (таймслот), иначе — по Москве."""
    return None if dt is None else utc(dt).astimezone(_zone(tz)).date()


# ── строки плана ──
async def current_items(db: AsyncSession, plan_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[SupplyPlanItem]]:
    out: dict[uuid.UUID, list[SupplyPlanItem]] = defaultdict(list)
    if not plan_ids:
        return out
    for item in (await db.execute(
        select(SupplyPlanItem).where(SupplyPlanItem.plan_id.in_(plan_ids), SupplyPlanItem.replaced_at.is_(None))
        .order_by(SupplyPlanItem.created_at)
    )).scalars():
        out[item.plan_id].append(item)
    return out


async def open_plan_qty(db: AsyncSession, user_id: uuid.UUID) -> dict[tuple[str, str, uuid.UUID], int]:
    """(тип, куда, товар) → штук в черновиках планов: их ещё не отправили — рекомендация не должна советовать их снова."""
    plans = {p.id: p for p in (await db.execute(
        select(SupplyPlan).where(SupplyPlan.user_id == user_id, SupplyPlan.status == PLAN_DRAFT)
    )).scalars()}
    out: dict[tuple[str, str, uuid.UUID], int] = defaultdict(int)
    for plan_id, items in (await current_items(db, list(plans))).items():
        p = plans[plan_id]
        for i in items:
            out[(p.kind, p.destination_key, i.product_id)] += i.qty
    return out


async def latest_bundle_qty(db: AsyncSession, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Заявка → {товар (или (sku, артикул) не из каталога): заявлено} по последнему снимку состава."""
    if not order_ids:
        return {}
    rows = (await db.execute(
        select(SupplyOrderItem).where(SupplyOrderItem.order_id.in_(order_ids), SupplyOrderItem.source == BUNDLE)
    )).scalars().all()
    last: dict[uuid.UUID, datetime] = {}
    for r in rows:
        last[r.order_id] = max(last.get(r.order_id, r.snapshot_at), r.snapshot_at)
    out: dict[uuid.UUID, dict] = defaultdict(dict)
    for r in rows:
        if r.snapshot_at == last[r.order_id]:
            key = r.product_id or (r.sku, r.offer_id)
            out[r.order_id][key] = out[r.order_id].get(key, 0) + r.quantity
    return out


async def preparing_order_qty(db: AsyncSession, user_id: uuid.UUID) -> dict[tuple[str, uuid.UUID], int]:
    """(кластер, товар) → штук в заявках Ozon, которые ещё готовятся (в остатках «в пути» их пока нет)."""
    orders = [o for o in (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == user_id))).scalars()
              if orders_engine.group_of(o.state) == orders_engine.PREPARING and o.cluster_name]
    qty = await latest_bundle_qty(db, [o.id for o in orders])
    out: dict[tuple[str, uuid.UUID], int] = defaultdict(int)
    for o in orders:
        for key, n in qty.get(o.id, {}).items():
            if isinstance(key, uuid.UUID):
                out[(cluster_key(o.cluster_name) or "", key)] += n
    return out


# ── планы ──
async def _plan(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> SupplyPlan:
    plan = await db.get(SupplyPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise PlanError("План поставки не найден", 404)
    return plan


async def _products(db: AsyncSession, user_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Product]:
    found = {p.id: p for p in (await db.execute(
        select(Product).where(Product.user_id == user_id, Product.id.in_(ids))
    )).scalars()} if ids else {}
    if len(found) != len(set(ids)):
        raise PlanError("Товар не найден в каталоге магазина — обновите страницу")
    return found


def _check_qty(qty: int) -> None:
    if qty < 0 or qty > MAX_QTY:
        raise PlanError(f"Количество — целое число штук от 0 до {MAX_QTY:,}".replace(",", " "))


async def create_plan(db: AsyncSession, user_id: uuid.UUID, *, kind: str, destination_key: str, destination_name: str,
                      items: list[dict], planned_date: date | None = None, note: str | None = None) -> SupplyPlan:
    if kind not in (engine.SUPPLY, engine.PURCHASE):
        raise PlanError("Тип плана — поставка в Ozon или закупка на свой склад")
    if not destination_key.strip() or not destination_name.strip():
        raise PlanError("Укажите, куда везти: кластер Ozon или свой склад")
    if not items or len(items) > MAX_ITEMS:
        raise PlanError(f"В плане — от 1 до {MAX_ITEMS} товаров")
    ids = [i["product_id"] for i in items]
    if len(set(ids)) != len(ids):
        raise PlanError("Товар повторяется в плане — оставьте одну строку")
    for i in items:
        _check_qty(i["qty"])
    if not any(i["qty"] > 0 for i in items):
        raise PlanError("В плане нет ни одной штуки — укажите количество хотя бы у одного товара")
    await _products(db, user_id, ids)
    now = _now()
    plan = SupplyPlan(user_id=user_id, kind=kind, destination_key=destination_key.strip()[:255],
                      destination_name=destination_name.strip()[:255], planned_date=planned_date,
                      note=(note or "").strip()[:NOTE_MAX] or None, status=PLAN_DRAFT, created_at=now, updated_at=now)
    db.add(plan)
    await db.flush()
    for i in items:
        db.add(SupplyPlanItem(plan_id=plan.id, product_id=i["product_id"], recommended=i.get("recommended"),
                              qty=i["qty"], created_at=now))
    await db.commit()
    return plan


def _editable(plan: SupplyPlan) -> None:
    if plan.status == PLAN_ORDERED:
        raise PlanError("По плану уже есть заявка в Ozon — состав меняйте в кабинете Ozon, здесь он обновится сам")
    if plan.status == PLAN_CANCELLED:
        raise PlanError("План отменён — верните его в работу, чтобы менять")


async def set_item(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID, product_id: uuid.UUID, qty: int) -> None:
    """Ручная корректировка: новая версия строки; товара нет в плане — добавляется."""
    plan = await _plan(db, user_id, plan_id)
    _editable(plan)
    _check_qty(qty)
    await _products(db, user_id, [product_id])
    current = (await db.execute(
        select(SupplyPlanItem).where(SupplyPlanItem.plan_id == plan.id, SupplyPlanItem.product_id == product_id,
                                     SupplyPlanItem.replaced_at.is_(None))
    )).scalar_one_or_none()
    if current is not None and current.qty == qty:
        return
    now = _now()
    if current is not None:
        current.replaced_at = now
    elif len((await current_items(db, [plan.id]))[plan.id]) >= MAX_ITEMS:
        raise PlanError(f"В плане — не больше {MAX_ITEMS} товаров")
    db.add(SupplyPlanItem(plan_id=plan.id, product_id=product_id, qty=qty, created_at=now,
                          recommended=current.recommended if current is not None else None))
    plan.updated_at = now
    await db.commit()


async def update_plan(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID, changes: dict) -> SupplyPlan:
    """Дата, заметка, статус (отменить / вернуть в работу), привязка заявки Ozon (ozon_order_id; None — отвязать)."""
    plan = await _plan(db, user_id, plan_id)
    now = _now()
    if "planned_date" in changes:
        plan.planned_date = changes["planned_date"]
    if "note" in changes:
        plan.note = (changes["note"] or "").strip()[:NOTE_MAX] or None
    if "status" in changes and changes["status"] != plan.status:
        new = changes["status"]
        if new == PLAN_CANCELLED and plan.status != PLAN_DRAFT:
            raise PlanError("Отменить можно план без заявки; заявку Ozon отменяют в кабинете Ozon")
        if new == PLAN_DRAFT and plan.status != PLAN_CANCELLED:
            raise PlanError("Вернуть в работу можно отменённый план")
        if new not in (PLAN_CANCELLED, PLAN_DRAFT):
            raise PlanError("Статус плана — в работе или отменён; «заявка создана» ставится сам")
        plan.status = new
    if "ozon_order_id" in changes:
        await _link(db, user_id, plan, changes["ozon_order_id"])
    plan.updated_at = now
    await db.commit()
    return plan


async def _link(db: AsyncSession, user_id: uuid.UUID, plan: SupplyPlan, ozon_order_id: int | None) -> None:
    """Заявку оформили в кабинете — привязать к плану: увидим, что привезли против плана."""
    old = (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == user_id, SupplyOrder.plan_id == plan.id))
           ).scalars().all()
    if ozon_order_id is None:
        for o in old:
            o.plan_id = None
        plan.ozon_order_id = None
        plan.status = PLAN_DRAFT if plan.status == PLAN_ORDERED else plan.status
        return
    if plan.status == PLAN_CANCELLED:
        raise PlanError("План отменён — верните его в работу, чтобы привязать заявку")
    order = (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == user_id,
                                                        SupplyOrder.ozon_order_id == ozon_order_id))).scalar_one_or_none()
    if order is None:
        raise PlanError("Такой заявки нет среди загруженных — обновите заявки на поставку и попробуйте снова")
    if order.plan_id is not None and order.plan_id != plan.id:
        raise PlanError("Заявка уже привязана к другому плану")
    for o in old:
        o.plan_id = None
    order.plan_id = plan.id
    plan.ozon_order_id, plan.status = ozon_order_id, PLAN_ORDERED


async def mark_ordered(db: AsyncSession, plan: SupplyPlan, ozon_order_id: int) -> None:
    """Заявку создали через сервис: план фиксируется (коммит — на вызывающем)."""
    plan.ozon_order_id, plan.status, plan.updated_at = ozon_order_id, PLAN_ORDERED, _now()
    order = (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == plan.user_id,
                                                        SupplyOrder.ozon_order_id == ozon_order_id))).scalar_one_or_none()
    if order is not None:
        order.plan_id = plan.id


def _product_out(p: Product) -> dict:
    return {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku}


async def _orders_by_plan(db: AsyncSession, user_id: uuid.UUID, plans: list[SupplyPlan]) -> dict[uuid.UUID, SupplyOrder]:
    ids = [p.ozon_order_id for p in plans if p.ozon_order_id]
    if not ids:
        return {}
    orders = (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == user_id,
                                                         SupplyOrder.ozon_order_id.in_(ids)))).scalars()
    by_ozon = {o.ozon_order_id: o for o in orders}
    return {p.id: by_ozon[p.ozon_order_id] for p in plans if p.ozon_order_id in by_ozon}


def _order_brief(o: SupplyOrder | None, ozon_order_id: int | None) -> dict | None:
    if ozon_order_id is None:
        return None
    if o is None:  # создали через сервис, синхронизация ещё не забрала
        return {"id": None, "ozon_order_id": ozon_order_id, "number": None, "state": None, "state_text": "Загружается",
                "group": orders_engine.PREPARING}
    return {"id": o.id, "ozon_order_id": o.ozon_order_id, "number": o.order_number, "state": o.state,
            "state_text": orders_engine.state_text(o.state), "group": orders_engine.group_of(o.state)}


def _plan_out(p: SupplyPlan, items: list[SupplyPlanItem], order: SupplyOrder | None) -> dict:
    live = [i for i in items if i.qty > 0]
    return {
        "id": p.id, "kind": p.kind, "kind_text": KIND_TEXT[p.kind], "destination_key": p.destination_key,
        "destination_name": p.destination_name, "planned_date": p.planned_date, "note": p.note, "status": p.status,
        "created_at": utc(p.created_at), "updated_at": utc(p.updated_at), "products": len(live),
        "units": sum(i.qty for i in live), "recommended": sum(i.recommended or 0 for i in items),
        "corrected": sum(1 for i in items if i.recommended is not None and i.qty != i.recommended),
        "order": _order_brief(order, p.ozon_order_id),
    }


async def list_plans(db: AsyncSession, user_id: uuid.UUID, status: str | None = None) -> list[dict]:
    q = select(SupplyPlan).where(SupplyPlan.user_id == user_id)
    if status:
        q = q.where(SupplyPlan.status == status)
    plans = (await db.execute(q.order_by(SupplyPlan.created_at.desc()))).scalars().all()
    items = await current_items(db, [p.id for p in plans])
    orders = await _orders_by_plan(db, user_id, plans)
    return [_plan_out(p, items.get(p.id, []), orders.get(p.id)) for p in plans]


async def plan_detail(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> dict:
    plan = await _plan(db, user_id, plan_id)
    all_rows = (await db.execute(select(SupplyPlanItem).where(SupplyPlanItem.plan_id == plan.id)
                                 .order_by(SupplyPlanItem.created_at))).scalars().all()
    current = [r for r in all_rows if r.replaced_at is None]
    products = await _products(db, user_id, list({r.product_id for r in all_rows}))
    versions: dict[uuid.UUID, list[SupplyPlanItem]] = defaultdict(list)
    for r in all_rows:
        versions[r.product_id].append(r)
    order = (await _orders_by_plan(db, user_id, [plan])).get(plan.id)
    out = _plan_out(plan, current, order)
    out["items"] = sorted((
        {"product": _product_out(products[r.product_id]), "recommended": r.recommended, "qty": r.qty,
         "updated_at": utc(r.created_at), "has_sku": bool(products[r.product_id].ozon_sku),
         "history": [{"qty": v.qty, "at": utc(v.created_at)} for v in versions[r.product_id]]}
        for r in current
    ), key=lambda i: (i["qty"] == 0, i["product"]["offer_id"]))
    out["editable"] = plan.status == PLAN_DRAFT
    out["can_create_in_ozon"] = plan.status == PLAN_DRAFT and plan.kind == engine.SUPPLY
    return out


def items_key(items: list[SupplyPlanItem]) -> str:
    """Состав плана для проверки «план не меняли после предпросмотра»."""
    raw = ",".join(sorted(f"{i.product_id}:{i.qty}" for i in items if i.qty > 0))
    return hashlib.sha256(raw.encode()).hexdigest()


# ── календарь ──
async def calendar(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    if date_to < date_from or (date_to - date_from).days > CALENDAR_MAX_DAYS:
        raise PlanError(f"Период календаря — до {CALENDAR_MAX_DAYS} дней")
    events: list[dict] = []
    plans = (await db.execute(select(SupplyPlan).where(
        SupplyPlan.user_id == user_id, SupplyPlan.status != PLAN_CANCELLED, SupplyPlan.planned_date.is_not(None),
        SupplyPlan.planned_date >= date_from, SupplyPlan.planned_date <= date_to,
    ))).scalars().all()
    items = await current_items(db, [p.id for p in plans])
    for p in plans:
        units = sum(i.qty for i in items.get(p.id, []))
        events.append({"date": p.planned_date, "kind": "plan", "title": f"План: {p.destination_name}", "units": units,
                       "plan_id": p.id, "order_id": None, "state": p.status, "state_text":
                       "заявка создана" if p.status == PLAN_ORDERED else "план", "group": None, "time": None})
    orders = (await db.execute(select(SupplyOrder).where(SupplyOrder.user_id == user_id))).scalars().all()
    qty = await latest_bundle_qty(db, [o.id for o in orders])
    accepted = await accepted_days(db, [o.id for o in orders])
    for o in orders:
        units = sum(qty.get(o.id, {}).values())
        where = o.cluster_name or o.storage_warehouse or "склад Ozon"
        slot = _local_day(o.timeslot_from, o.timezone)
        if slot and date_from <= slot <= date_to and orders_engine.group_of(o.state) != orders_engine.FAILED:
            events.append({"date": slot, "kind": "timeslot", "title": f"Заявка №{o.order_number or o.ozon_order_id}: {where}",
                           "units": units, "plan_id": o.plan_id, "order_id": o.id, "state": o.state,
                           "state_text": orders_engine.state_text(o.state), "group": orders_engine.group_of(o.state),
                           "time": _slot_time(o)})
        done = accepted.get(o.id)
        if done and date_from <= done <= date_to and done != slot:
            events.append({"date": done, "kind": "accepted", "title": f"Принята: №{o.order_number or o.ozon_order_id}",
                           "units": units, "plan_id": o.plan_id, "order_id": o.id, "state": o.state,
                           "state_text": orders_engine.state_text(o.state), "group": orders_engine.group_of(o.state),
                           "time": None})
    events.sort(key=lambda e: (e["date"], {"accepted": 0, "timeslot": 1, "plan": 2}[e["kind"]], e["title"]))
    return {"date_from": date_from, "date_to": date_to, "events": events}


def _slot_time(o: SupplyOrder) -> str | None:
    if o.timeslot_from is None:
        return None
    tz = _zone(o.timezone)
    start = utc(o.timeslot_from).astimezone(tz)
    end = utc(o.timeslot_to).astimezone(tz) if o.timeslot_to else None
    return f"{start:%H:%M}" + (f"–{end:%H:%M}" if end else "")


async def accepted_days(db: AsyncSession, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, date]:
    """Когда поставку приняли на склад: первая запись истории со статусом приёмки или «завершена»."""
    if not order_ids:
        return {}
    out: dict[uuid.UUID, date] = {}
    for e in (await db.execute(
        select(SupplyOrderEvent).where(SupplyOrderEvent.order_id.in_(order_ids)).order_by(SupplyOrderEvent.seen_at)
    )).scalars():
        if e.order_id in out:
            continue
        if orders_engine.group_of(e.state) in (orders_engine.ACCEPTING, orders_engine.DONE):
            out[e.order_id] = _local_day(e.state_updated_at or e.seen_at)
    return out


# ── Excel плана ──
PLAN_COLUMNS = [
    Col("Артикул", 26), Col("Название", 40), Col("SKU Ozon", 14), Col("Советовал расчёт, шт.", 13, "int"),
    Col("В плане, шт.", 11, "int"),
]


def plan_xlsx(plan: dict) -> bytes:
    rows = [[i["product"]["offer_id"], i["product"]["name"], i["product"]["ozon_sku"], i["recommended"], i["qty"]]
            for i in plan["items"] if i["qty"] > 0]
    notes = [
        f"{plan['kind_text']}: {plan['destination_name']}",
        f"Дата: {plan['planned_date']:%d.%m.%Y}" if plan["planned_date"] else "Дата не назначена",
        f"Товаров: {plan['products']}, штук: {plan['units']}",
        "«Советовал расчёт» — сколько рекомендовал расчёт поставок, когда товар попал в план; «В плане» — с вашей "
        "корректировкой.",
    ] + ([f"Заметка: {plan['note']}"] if plan["note"] else [])
    total = ["Итого", None, None, None, plan["units"]]
    return table_xlsx("План поставки", PLAN_COLUMNS, rows, total=total, notes_title="О плане", notes=notes)

