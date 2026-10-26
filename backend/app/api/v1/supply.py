"""Поставки (шаги 5.2, 10.18): что, куда и сколько везти; Excel для логиста. 10.18: планы поставок с ручной
корректировкой, заявки на поставку FBO из Ozon (статусы, история, что привезли против плана), календарь, создание заявки в
Ozon по плану — проверка (черновик) → таймслот → подтверждение → журнал (правило 6)."""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.engine.supply_planner import Params
from app.models.user import User
from app.schemas.supply import (
    CreateOrderIn,
    CreateOrderOut,
    DraftOut,
    PlanCreate,
    PlanDetail,
    PlanItemUpdate,
    PlanOut,
    PlanUpdate,
    SupplyCalendar,
    SupplyOrderDetail,
    SupplyOrdersList,
    SupplyReport,
    Timeslots,
)
from app.services import audit
from app.services import supply as svc
from app.services import supply_history, supply_ozon, supply_plans
from app.services.stores import get_current_store
from app.services.supply_plans import PlanError
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/supply", tags=["supply"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _fail(exc: PlanError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


class _ParamsQuery:
    """Параметры из запроса; не переданные — сохранённые у пользователя (Основные настройки → Поставки)."""

    def __init__(
        self,
        target_days: int | None = Query(None, ge=1, le=365, description="Запас после поставки, дней продаж"),
        lead_time_days: int | None = Query(None, ge=0, le=180, description="Сколько дней поставка в пути"),
        safety_days: int | None = Query(None, ge=0, le=180, description="Страховой запас сверх цели, дней"),
    ):
        self.target_days, self.lead_time_days, self.safety_days = target_days, lead_time_days, safety_days

    async def params(self, db: AsyncSession, user: User) -> Params:
        base = await svc.load_params(db, user.id)
        return svc.with_overrides(base, target_days=self.target_days, lead_time_days=self.lead_time_days,
                                  safety_days=self.safety_days)


@router.get("/recommendations", response_model=SupplyReport)
async def recommendations(
    q: _ParamsQuery = Depends(), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Рекомендации по паре товар × кластер Ozon (поставка) и товар × свой склад (закупка): продажи за 30 и 7 дней,
    на сколько дней хватит, сколько везти, срочность; сколько уже в планах и заявках. Только товары с заказами за 30 дней
    (BUSINESS_LOGIC §5)."""
    return await svc.report(db, user.id, await q.params(db, user))


@router.get("/export", response_class=Response)
async def export(q: _ParamsQuery = Depends(), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Те же рекомендации в Excel для логиста: по кластеру и складу, срочные первыми; на втором листе — параметры."""
    data = await svc.report(db, user.id, await q.params(db, user))
    return attachment(svc.to_xlsx(data), XLSX, f"Поставки {today_msk():%d.%m.%Y}.xlsx", "supply.xlsx")


# ── планы ──
@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    status: str | None = Query(None, pattern="^(draft|ordered|cancelled)$"),
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db),
):
    return await supply_plans.list_plans(db, user.id, status)


@router.post("/plans", response_model=PlanDetail, status_code=201)
async def create_plan(data: PlanCreate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """План поставки из рекомендаций (с вашими количествами): куда, что, сколько, когда."""
    try:
        plan = await supply_plans.create_plan(
            db, user.id, kind=data.kind, destination_key=data.destination_key, destination_name=data.destination_name,
            items=[i.model_dump() for i in data.items], planned_date=data.planned_date, note=data.note,
        )
        return await supply_plans.plan_detail(db, user.id, plan.id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.get("/plans/{plan_id}", response_model=PlanDetail)
async def plan_detail(plan_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        return await supply_plans.plan_detail(db, user.id, plan_id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.patch("/plans/{plan_id}", response_model=PlanDetail)
async def update_plan(plan_id: uuid.UUID, data: PlanUpdate, user: User = Depends(get_current_store),
                      db: AsyncSession = Depends(get_db)):
    """Дата, заметка, отменить или вернуть в работу, привязать заявку Ozon, оформленную в кабинете (null — отвязать)."""
    try:
        await supply_plans.update_plan(db, user.id, plan_id, data.model_dump(exclude_unset=True))
        return await supply_plans.plan_detail(db, user.id, plan_id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.put("/plans/{plan_id}/items/{product_id}", response_model=PlanDetail)
async def set_plan_item(plan_id: uuid.UUID, product_id: uuid.UUID, data: PlanItemUpdate,
                        user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Ручная корректировка количества (новая версия строки; 0 — убрать товар из плана). Товара нет в плане — добавится."""
    try:
        await supply_plans.set_item(db, user.id, plan_id, product_id, data.qty)
        return await supply_plans.plan_detail(db, user.id, plan_id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.get("/plans/{plan_id}/export", response_class=Response)
async def export_plan(plan_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        plan = await supply_plans.plan_detail(db, user.id, plan_id)
    except PlanError as exc:
        raise _fail(exc) from None
    return attachment(supply_plans.plan_xlsx(plan), XLSX, f"План поставки — {plan['destination_name']}.xlsx",
                      "supply-plan.xlsx")


# ── заявка в Ozon по плану ──
@router.post("/plans/{plan_id}/ozon-check", response_model=DraftOut, dependencies=[Depends(audit.read_only)])
async def check_in_ozon(plan_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Проверить план в Ozon: черновик заявки (в кабинете не виден, живёт 30 минут) — склады кластера, что Ozon не примет."""
    try:
        return await supply_ozon.check(db, user.id, plan_id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.get("/drafts/{draft_id}", response_model=DraftOut)
async def draft_status(draft_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        return await supply_ozon.refresh(db, user.id, draft_id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.get("/drafts/{draft_id}/timeslots", response_model=Timeslots)
async def draft_timeslots(
    draft_id: uuid.UUID, warehouse_id: int = Query(ge=1), date_from: date | None = None, date_to: date | None = None,
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db),
):
    try:
        return await supply_ozon.timeslots(db, user.id, draft_id, warehouse_id, date_from, date_to)
    except PlanError as exc:
        raise _fail(exc) from None


@router.post("/drafts/{draft_id}/create", response_model=CreateOrderOut)
async def create_in_ozon(draft_id: uuid.UUID, data: CreateOrderIn, request: Request,
                         user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Создать заявку в Ozon по проверенному плану: склад, таймслот, подтверждение; один раз; запись в журнал."""
    try:
        return await supply_ozon.create(db, user.id, draft_id, warehouse_id=data.warehouse_id,
                                        slot_from=data.timeslot_from, slot_to=data.timeslot_to, confirm=data.confirm,
                                        request=request)
    except PlanError as exc:
        raise _fail(exc) from None


# ── заявки Ozon и календарь ──
@router.get("/orders", response_model=SupplyOrdersList)
async def orders(
    group: str | None = Query(None, pattern="^(preparing|on_the_way|accepting|done|failed)$"),
    search: str | None = Query(None, max_length=100),
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db),
):
    """Заявки на поставку FBO из Ozon: статус, даты, таймслот, склад и кластер, заявлено и принято, план сервиса."""
    return await supply_history.list_orders(db, user.id, group, search)


@router.get("/orders/export", response_class=Response)
async def orders_export(
    group: str | None = Query(None, pattern="^(preparing|on_the_way|accepting|done|failed)$"),
    search: str | None = Query(None, max_length=100),
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db),
):
    data = await supply_history.list_orders(db, user.id, group, search)
    return attachment(supply_history.to_xlsx(data), XLSX, f"Заявки на поставку {today_msk():%d.%m.%Y}.xlsx",
                      "supply-orders.xlsx")


@router.get("/orders/{order_id}", response_model=SupplyOrderDetail)
async def order_detail(order_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Заявка: история статусов, что привезли против плана (план → заявлено → принято), как продавалось после приёмки."""
    try:
        return await supply_history.order_detail(db, user.id, order_id)
    except PlanError as exc:
        raise _fail(exc) from None


@router.get("/calendar", response_model=SupplyCalendar)
async def supply_calendar(
    date_from: date | None = None, date_to: date | None = None,
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db),
):
    """Календарь поставок: даты планов, таймслоты заявок Ozon, дни приёмки. По умолчанию — от 14 дней назад на 42 вперёд."""
    today = today_msk()
    start = date_from or today - timedelta(days=14)
    end = date_to or start + timedelta(days=55)
    try:
        return await supply_plans.calendar(db, user.id, start, end)
    except PlanError as exc:
        raise _fail(exc) from None
