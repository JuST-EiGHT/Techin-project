"""Планирование поставок (шаг 5.2): ответ /api/v1/supply/recommendations."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.stock import StockProduct, Thresholds


class SupplyParams(BaseModel):
    target_days: int  # запас после поставки, дней продаж
    lead_time_days: int  # сколько дней поставка в пути
    safety_days: int = 0  # страховой запас сверх цели, дней (10.18)


class PostingsCoverage(BaseModel):
    loaded_from: date  # отправления загружены с этого дня
    synced_at: datetime
    window_from: date  # окно расчёта: 30 дней, но не раньше загруженного
    window_to: date
    days: int
    enough: bool  # дней с данными хватает (от 7)


class SupplyDestination(BaseModel):
    key: str
    name: str  # кластер Ozon или свой склад
    warehouse_type: str | None  # FBS | rFBS — у своего склада
    warehouses: list[str]  # склады Ozon кластера, где сейчас лежит этот товар


class SupplyItem(BaseModel):
    product: StockProduct
    kind: str  # supply — поставка в кластер Ozon, purchase — закупка на свой склад
    urgency: str  # critical | urgent | planned
    destination: SupplyDestination
    sold_30: int  # заказано без отмен за окно (до 30 дней)
    sold_7: int
    avg_daily: Decimal  # продажи в день за 30 дней
    rate: Decimal  # за 7 дней
    trend: Decimal | None
    stock: int  # там сейчас
    in_transit: int
    days_left: Decimal | None  # хватит на, дней; null — за 7 дней не продавалось
    target_stock: int
    need: int  # нужно к приходу поставки
    to_order: int  # отправить / закупить
    arrives_late: bool  # кончится раньше, чем доедет поставка
    in_plans: int = 0  # уже в открытых планах поставки сервиса (10.18)
    in_orders: int = 0  # уже в заявках Ozon, которые ещё готовятся
    still_needed: int = 0  # нужно ещё: to_order − в планах − в заявках


class SupplyTotals(BaseModel):
    critical: int
    urgent: int
    planned: int
    supply_units: int
    purchase_units: int
    products: int


class SupplyReport(BaseModel):
    params: SupplyParams
    saved: SupplyParams
    is_saved: bool
    thresholds: Thresholds
    has_key: bool
    postings_known: bool  # отправления FBO хоть раз загружались
    fbo: PostingsCoverage | None
    fbs: PostingsCoverage | None
    has_own_warehouses: bool
    totals: SupplyTotals
    today: date
    items: list[SupplyItem]


# ── 10.18: планы поставок, заявки Ozon, календарь ──
class PlanItemIn(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(ge=0, le=100_000)
    recommended: int | None = Field(None, ge=0, le=1_000_000)  # сколько советовал расчёт


class PlanCreate(BaseModel):
    kind: Literal["supply", "purchase"]
    destination_key: str = Field(min_length=1, max_length=255)
    destination_name: str = Field(min_length=1, max_length=255)
    planned_date: date | None = None
    note: str | None = Field(None, max_length=1000)
    items: list[PlanItemIn] = Field(min_length=1, max_length=500)


class PlanUpdate(BaseModel):
    """Не переданное поле не меняется; ozon_order_id: null — отвязать заявку."""

    planned_date: date | None = None
    note: str | None = Field(None, max_length=1000)
    status: Literal["draft", "cancelled"] | None = None
    ozon_order_id: int | None = Field(None, ge=1)


class PlanItemUpdate(BaseModel):
    qty: int = Field(ge=0, le=100_000)


class OrderBrief(BaseModel):
    id: uuid.UUID | None
    ozon_order_id: int
    number: str | None
    state: str | None
    state_text: str
    group: str


class PlanOut(BaseModel):
    id: uuid.UUID
    kind: str
    kind_text: str
    destination_key: str
    destination_name: str
    planned_date: date | None
    note: str | None
    status: str  # draft | ordered | cancelled
    created_at: datetime
    updated_at: datetime
    products: int
    units: int
    recommended: int  # советовал расчёт, шт.
    corrected: int  # строк, где количество поправили вручную
    order: OrderBrief | None


class PlanItemVersion(BaseModel):
    qty: int
    at: datetime


class PlanItemOut(BaseModel):
    product: StockProduct
    recommended: int | None
    qty: int
    updated_at: datetime
    has_sku: bool
    history: list[PlanItemVersion]


class PlanDetail(PlanOut):
    items: list[PlanItemOut]
    editable: bool
    can_create_in_ozon: bool


class DraftWarehouse(BaseModel):
    id: int
    name: str
    address: str | None
    available: bool
    state: str | None
    reason: str | None
    rank: int | None


class DraftRejected(BaseModel):
    sku: int
    offer_id: str | None
    product_id: uuid.UUID | None
    reasons: list[str]


class DraftOut(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    status: str  # pending | ready | failed | used
    cluster_name: str | None
    warehouses: list[DraftWarehouse]
    rejected: list[DraftRejected]
    errors: list[str]
    created_at: datetime
    expires_at: datetime
    ozon_order_id: int | None


class TimeslotSlot(BaseModel):
    model_config = {"populate_by_name": True}

    from_: str = Field(alias="from", serialization_alias="from")
    to: str


class TimeslotDay(BaseModel):
    date: str
    slots: list[TimeslotSlot]


class Timeslots(BaseModel):
    timezone: str | None
    days: list[TimeslotDay]


class CreateOrderIn(BaseModel):
    warehouse_id: int = Field(ge=1)
    timeslot_from: str = Field(min_length=1, max_length=40)
    timeslot_to: str = Field(min_length=1, max_length=40)
    confirm: bool = False


class CreateOrderOut(BaseModel):
    plan_id: uuid.UUID
    ozon_order_id: int
    message: str


class OrderPlanBrief(BaseModel):
    id: uuid.UUID
    destination_name: str


class SupplyOrderOut(BaseModel):
    id: uuid.UUID
    ozon_order_id: int
    number: str | None
    created_at: datetime | None
    state: str
    state_text: str
    group: str  # preparing | on_the_way | accepting | done | failed
    state_updated_at: datetime | None
    timeslot_from: datetime | None
    timeslot_to: datetime | None
    timeslot_day: date | None  # по часовому поясу склада
    timeslot_time: str | None
    dropoff: str | None
    storage_warehouse: str | None
    cluster: str | None
    accepted_on: date | None
    products: int | None
    declared: int | None  # заявлено, шт.; null — состав не загружен
    accepted: int | None  # принято по актам; null — актов ещё нет
    plan: OrderPlanBrief | None
    synced_at: datetime


class SupplyOrdersList(BaseModel):
    counts: dict[str, int]
    total: int
    items: list[SupplyOrderOut]
    group_text: dict[str, str]


class OrderEvent(BaseModel):
    state: str
    state_text: str
    at: datetime
    timeslot_from: datetime | None


class SalesAfter(BaseModel):
    known: bool
    after_days: int
    after_days_with_data: int | None = None
    after_qty: int | None = None
    per_day_after: Decimal | None = None
    before_qty: int | None = None  # за такое же число дней с данными до приёмки
    change_pct: Decimal | None = None


class OrderResultItem(BaseModel):
    product: dict  # id может быть null — товара нет в каталоге
    planned: int | None
    declared: int | None
    accepted: int | None
    diff: int | None  # принято − заявлено
    plan_diff: int | None  # заявлено − в плане
    defect: bool
    sales_after: SalesAfter | None
    lasts_days: int | None  # принятого хватит на, дней (при продажах после приёмки)


class OrderTotals(BaseModel):
    planned: int | None
    declared: int | None
    accepted: int | None
    diff: int | None
    short: int


class SupplyOrderDetail(SupplyOrderOut):
    events: list[OrderEvent]
    items: list[OrderResultItem]
    totals: OrderTotals
    has_acts: bool


class CalendarEvent(BaseModel):
    date: date
    kind: str  # plan | timeslot | accepted
    title: str
    units: int
    plan_id: uuid.UUID | None
    order_id: uuid.UUID | None
    state: str | None
    state_text: str
    group: str | None
    time: str | None


class SupplyCalendar(BaseModel):
    date_from: date
    date_to: date
    events: list[CalendarEvent]
