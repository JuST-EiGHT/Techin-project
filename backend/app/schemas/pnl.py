"""P&L за период (шаг 3.2): ответы /api/v1/pnl, /pnl/by-day, /pnl/periods."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.report import MissingCost, PnLOut

Source = Literal["auto", "xlsx", "api"]
DataSource = Literal["xlsx", "api", "mixed", "none"]


class DateRange(BaseModel):
    date_from: date
    date_to: date


class Coverage(BaseModel):
    """Откуда взяты дни периода. Нет данных за день — пропуск, а не 0."""

    days_total: int
    xlsx_days: int
    api_days: int
    missing_days: int
    missing: list[DateRange]


class PeriodReport(BaseModel):
    """Отчёт XLSX, строки которого вошли в период."""

    id: uuid.UUID
    period: str
    period_start: date
    period_end: date
    manual_total_applied: bool  # его ручная сумма «сколько пришло» учтена


class PeriodPnL(BaseModel):
    date_from: date
    date_to: date
    source: Source  # запрошенный
    data_source: DataSource  # использованный: xlsx, api, mixed — по дням из обоих, none — данных нет
    coverage: Coverage
    reports: list[PeriodReport]
    auto_total: Decimal | None  # сумма начислений за период; null — данных нет
    revenue_base: Decimal | None
    cost_sales: Decimal | None  # себестоимость проданного без НДС
    by_type: dict[str, Decimal]
    missing_cost: list[MissingCost]
    pnl: PnLOut | None  # null — данных нет
    warnings: list[str]
    calculated_at: datetime
    from_snapshot: bool  # из сохранённого итога закрытого месяца


class DayPnL(BaseModel):
    """День периода; source = null — данных за день нет (все суммы null, а не 0)."""

    date: date
    source: Literal["xlsx", "api"] | None
    revenue: Decimal | None  # «Выручка»
    total: Decimal | None  # сумма начислений за день
    owed: Decimal | None  # себестоимость с НДС проданного в этот день
    profit_before_tax: Decimal | None


class PnLByDay(BaseModel):
    date_from: date
    date_to: date
    source: Source
    data_source: DataSource
    days: list[DayPnL]


class PeriodsReport(BaseModel):
    id: uuid.UUID
    report_type: Literal["accrual", "realization"]
    period: str
    period_start: date
    period_end: date


class SnapshotState(BaseModel):
    calculated_at: datetime
    stale: bool  # данные периода изменились — пересчитается при следующем запросе


class MonthCoverage(BaseModel):
    month: str  # ГГГГ-ММ
    date_from: date
    date_to: date
    closed: bool
    xlsx_days: int
    api_days: int
    missing_days: int
    snapshot: SnapshotState | None


class PnLPeriods(BaseModel):
    """За какие периоды есть данные: отчёты XLSX, дни из API, месяцы (новые сверху)."""

    reports: list[PeriodsReport]
    api: list[DateRange]
    first_day: date | None
    last_day: date | None
    months: list[MonthCoverage]


class PerUnit(BaseModel):
    """Юнит-экономика (шаг 3.7a): на 1 проданную штуку; *_pct — доля от средней цены, %."""

    price: Decimal  # средняя цена
    commission: Decimal
    commission_pct: Decimal | None
    logistics: Decimal
    logistics_pct: Decimal | None
    other: Decimal
    other_pct: Decimal | None
    total: Decimal  # начислено Ozon на штуку
    total_pct: Decimal | None
    cost: Decimal | None  # себестоимость штуки; null — не указана
    cost_pct: Decimal | None
    profit: Decimal | None  # прибыль до налога на штуку
    profit_pct: Decimal | None


class ProductRow(BaseModel):
    """Товар за период (шаг 3.4). Нет себестоимости — cost, profit, margin = null, а не 0."""

    offer_id: str
    name: str
    sku: str
    qty: int  # продано, шт. (строки «Выручка»)
    returned_qty: int
    revenue: Decimal
    avg_price: Decimal | None
    commission: Decimal  # вознаграждение Ozon (+ его возврат)
    logistics: Decimal
    other: Decimal  # баллы за скидки, эквайринг, продвижение, возвраты выручки и др.
    total: Decimal  # начислено Ozon по товару
    by_posting: Decimal  # из них отнесено к товару по номеру отправления
    cost: Decimal | None
    unit_cost: Decimal | None
    cost_missing: bool
    profit: Decimal | None  # до налога: начислено Ozon − себестоимость
    margin: Decimal | None  # % от выручки
    by_type: dict[str, Decimal]
    per_unit: PerUnit | None = None  # штук не продано — null, а не 0


class ProductTotals(BaseModel):
    products: int
    qty: int
    revenue: Decimal
    commission: Decimal
    logistics: Decimal
    other: Decimal
    total: Decimal
    cost: Decimal  # по товарам с себестоимостью
    profit: Decimal
    margin: Decimal | None
    missing_cost: int


class Unallocated(BaseModel):
    """Начисления без товара (хранение, подписки, страхование, отправления с несколькими товарами)."""

    total: Decimal
    lines: int
    multi_product: Decimal
    by_type: dict[str, Decimal]


class ProductsPage(BaseModel):
    date_from: date
    date_to: date
    source: Source
    report_id: uuid.UUID | None
    data_source: DataSource
    coverage: Coverage
    cost_basis: Literal["with_vat", "without_vat"]  # без НДС — у плательщиков НДС с вычетом
    supplier_vat: Decimal
    items: list[ProductRow]
    total: int  # товаров после поиска
    limit: int
    offset: int
    totals: ProductTotals  # по всем товарам периода
    unallocated: Unallocated
    period_total: Decimal  # все начисления периода = товары + не распределено
    period_by_type: dict[str, Decimal] = {}  # все начисления периода по статьям: удержания — первыми
    warnings: list[str]
