"""Продажи по дням (шаг 3.5): ответы /api/v1/sales/*."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class SalesDay(BaseModel):
    """День; source = null — данных за день нет (все значения null, а не 0)."""

    date: date
    source: Literal["xlsx", "api"] | None
    qty_sold: int | None
    qty_returned: int | None
    revenue: Decimal | None  # «Выручка»
    returns_amount: Decimal | None  # «Возврат выручки», положительная


class SalesTotals(BaseModel):
    qty_sold: int
    qty_returned: int
    revenue: Decimal
    days_with_data: int
    days_without_data: int
    avg_daily: Decimal | None  # (продано − возвращено) / дней с данными


class SalesDaily(BaseModel):
    date_from: date
    date_to: date
    offer_id: str | None  # null — все товары
    data_source: Literal["xlsx", "api", "mixed", "none"]
    days: list[SalesDay]
    totals: SalesTotals


class SalesRebuild(BaseModel):
    days: int
    days_with_data: int
    rows: int
    unattributed: int  # строк продаж и возвратов без товара — их не к чему отнести
