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
