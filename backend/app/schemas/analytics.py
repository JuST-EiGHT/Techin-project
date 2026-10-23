"""Основа аналитики (шаг 10.3): показатели по дням, неделям и месяцам с прошлым периодом. Деньги — строками («1234.50»)."""
from datetime import date
from typing import Literal

from pydantic import BaseModel


class Values(BaseModel):
    revenue: str | None  # выручка, ₽; None — нет данных
    profit: str | None  # прибыль: в точках — до налога, в итогах — см. profit_kind
    margin: str | None  # маржинальность, %
    orders: int | None  # заказы (отправления без отмен); None — отправления не загружены
    ordered_qty: int | None
    sold_qty: int | None  # продано, шт.
    returned_qty: int | None
    returns_amount: str | None  # возвраты, ₽
    avg_price: str | None  # выручка / проданные штуки


class PrevPoint(Values):
    date_from: date
    date_to: date
    partial: bool


class Point(Values):
    date_from: date
    date_to: date
    partial: bool  # неделя или месяц обрезаны краем периода
    days: int
    missing_days: int  # дней без начислений — пропуски, а не ноль
    orders_missing_days: int
    prev: PrevPoint | None = None  # та же по счёту точка прошлого периода (compare=1)


class Totals(Values):
    missing_days: int
    orders_missing_days: int
    missing_cost: int  # товаров без себестоимости — их прибыль не посчитана


class Range(BaseModel):
    date_from: date
    date_to: date


class Coverage(BaseModel):
    days_total: int
    xlsx_days: int
    api_days: int
    missing_days: int
    missing: list[Range]
    orders_missing_days: int
    data_source: str  # xlsx | api | mixed | none


class Previous(BaseModel):
    date_from: date
    date_to: date
    coverage: Coverage
    totals: Totals


class Changes(BaseModel):
    revenue: str | None
    profit: str | None
    orders: str | None
    ordered_qty: str | None
    sold_qty: str | None
    returned_qty: str | None
    returns_amount: str | None
    avg_price: str | None
    margin_pp: str | None  # маржа — в процентных пунктах


class FilterProduct(BaseModel):
    id: str
    offer_id: str
    name: str | None


class FilterCategory(BaseModel):
    id: int  # 0 — «Без категории»
    name: str
    path: str | None


class Filter(BaseModel):
    products: list[FilterProduct] | None
    category: FilterCategory | None
    product_count: int | None  # товаров в фильтре; None — магазин целиком


class Metrics(BaseModel):
    date_from: date
    date_to: date
    granularity: Literal["day", "week", "month"]
    filter: Filter
    coverage: Coverage
    profit_kind: Literal["net", "before_tax"]
    totals: Totals
    points: list[Point]
    previous: Previous | None
    changes: Changes | None
    compare_note: str | None = None


class Category(BaseModel):
    id: int
    name: str
    path: str | None
    products: int


class ProductOption(BaseModel):
    id: str
    offer_id: str
    name: str | None
    category_id: int | None
    is_archived: bool
