import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, model_validator

API_WARNING = (
    "Данные получены из API Ozon и могут расходиться с личным кабинетом. "
    "Для точного P&L загрузите XLSX-отчёт (по начислениям или о реализации)."
)


class ApiSourced(BaseModel):
    """Все ответы с данными из API Ozon помечены источником и предупреждением."""

    data_source: Literal["api"] = "api"
    warning: str = API_WARNING


class Money(BaseModel):
    income: Decimal  # сумма положительных начислений
    expense: Decimal  # сумма отрицательных (со знаком минус)
    net: Decimal


class AccrualProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class AccrualItem(BaseModel):
    id: uuid.UUID
    accrual_date: date
    type_id: int
    type_name: str
    category: str | None  # income | expense (по знаку сумм этого типа)
    amount: Decimal
    quantity: int | None
    posting_number: str | None
    sku: int | None
    product: AccrualProduct | None


class AccrualsResponse(ApiSourced):
    date_from: date
    date_to: date
    total: int  # строк по фильтру (для пагинации)
    limit: int
    offset: int
    totals: Money
    items: list[AccrualItem]


class Period(BaseModel):
    date_from: date
    date_to: date


class DayTotals(BaseModel):
    date: date
    income: Decimal
    expense: Decimal
    net: Decimal
    lines: int


class AccrualsByDayResponse(ApiSourced):
    date_from: date
    date_to: date
    totals: Money
    days: list[DayTotals]
    missing_periods: list[Period]  # дни, за которые начисления из API ещё не загружены


class FinanceSyncRequest(BaseModel):
    """Без дат — вчера, сегодня и незагруженные дни за 90 дней; с датами — период заново."""

    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def both_or_none(self) -> "FinanceSyncRequest":
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("укажите обе даты периода или ни одной")
        return self


# ---------- «Финансы → Обзор» (шаг 10.22) ----------


class FigureChange(BaseModel):
    value: Decimal | None  # null — данных за период нет
    previous: Decimal | None  # null — в прошлом периоде данных нет
    change_pct: Decimal | None = None  # изменение к прошлому периоду, %
    change_pp: Decimal | None = None  # у рентабельности — в процентных пунктах


class OverviewFigures(BaseModel):
    revenue: FigureChange
    expenses: FigureChange  # выручка − прибыль: Ozon, себестоимость, расходы бизнеса, налог
    profit: FigureChange
    margin: FigureChange  # прибыль / выручка, %


class OverviewPrevious(BaseModel):
    date_from: date
    date_to: date
    has_data: bool


class OverviewBusinessLoose(BaseModel):
    count: int
    amount: Decimal


class OverviewBusiness(BaseModel):
    total: Decimal
    operations: int
    uncategorized: OverviewBusinessLoose


class StructureItem(BaseModel):
    key: str  # строка P&L Ozon (returns, adjustments, commission …), cost, business:<id статьи>, tax
    title: str
    kind: Literal["ozon", "cost", "business", "tax"]
    amount: Decimal  # больше нуля — расход; меньше — доплаты Ozon (баллы за скидки, компенсации): уменьшают расходы
    share: Decimal | None  # % выручки
    category_id: uuid.UUID | None  # статья расходов бизнеса — для перехода к её операциям


class FinanceOverview(BaseModel):
    date_from: date
    date_to: date
    previous: OverviewPrevious
    data_source: Literal["xlsx", "api", "mixed", "none"]
    coverage: dict
    has_data: bool
    profit_kind: Literal["net", "before_tax"] | None  # net — после налога и расходов бизнеса; before_tax — налог не задан
    tax_set: bool
    figures: OverviewFigures
    profit_ozon: Decimal | None  # прибыль от продаж на Ozon до налога — без расходов бизнеса
    business: OverviewBusiness | None  # null — «Планирование» выключено: своих расходов нет
    structure: list[StructureItem]
    missing_cost: int
    warnings: list[str]
