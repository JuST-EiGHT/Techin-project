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
