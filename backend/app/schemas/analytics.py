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


# ── «Обзор бизнеса»: лучшие и худшие товары (шаг 10.7) ──
class LeaderProduct(BaseModel):
    id: str | None  # товара нет в каталоге (продажа по артикулу из отчёта) — None
    offer_id: str
    name: str | None


class ProfitLeader(BaseModel):
    product: LeaderProduct
    revenue: str
    profit: str
    margin: str | None
    qty: int


class SalesLeader(BaseModel):
    product: LeaderProduct
    qty: int  # продано сейчас, шт.
    prev_qty: int  # в прошлом периоде
    expected: str  # ожидали за дни с данными сейчас
    change_pct: str | None  # раньше не продавалось — null


class ProfitLeaders(BaseModel):
    best: list[ProfitLeader]
    worst: list[ProfitLeader]
    missing_cost: int  # товаров без себестоимости — в лидерах не участвуют
    data_source: str


class SalesLeaders(BaseModel):
    compared: bool  # хватает ли дней с данными в обоих периодах
    note: str | None
    growth: list[SalesLeader]
    decline: list[SalesLeader]
    days: int
    prev_days: int


class Leaders(BaseModel):
    date_from: str
    date_to: str
    previous: Range
    profit: ProfitLeaders
    sales: SalesLeaders


# ── «Аналитика продаж» (шаг 10.8) ──
class SalesRow(BaseModel):
    product: LeaderProduct
    orders: int | None  # отправления без отмен; отправления не загружены — null
    ordered_qty: int | None
    sold_qty: int | None  # по начислениям; дней с данными нет — null
    returned_qty: int | None
    return_share: str | None  # возвращено / продано, %
    revenue: str | None
    returns_amount: str | None
    avg_price: str | None
    prev_sold_qty: int | None
    expected: str | None  # ожидали с поправкой на дни с данными
    change_pct: str | None
    direction: str | None  # up | down — заметное изменение; null — в пределах колебаний или не сравнивали


class SalesTotals(BaseModel):
    orders: int
    ordered_qty: int
    sold_qty: int
    returned_qty: int
    revenue: str
    return_share: str | None


class SalesTable(BaseModel):
    date_from: str
    date_to: str
    previous: Range
    days: int
    prev_days: int
    orders_days: int
    compared: bool
    items: list[SalesRow]
    totals: SalesTotals


class SalesEventItem(BaseModel):
    product: LeaderProduct
    text: str
    link: str


class SalesEvent(BaseModel):
    day: str
    kind: str  # price | promo_in | promo_out | stockout | restock
    title: str
    count: int  # товаров в событии (в items — до 10)
    link: str
    items: list[SalesEventItem]


# ── «Аналитика прибыльности» (шаг 10.9) ──
class ProfitType(BaseModel):
    name: str  # статья начислений Ozon
    amount: str  # расход, ₽ (минус — Ozon доплатил)


class ProfitGroup(BaseModel):
    key: str  # cost | commission | logistics | ads | storage | returns | other | adjustment
    title: str
    amount: str
    share: str | None  # доля выручки, %
    prev_amount: str | None
    types: list[ProfitType]


class ProfitPrevious(BaseModel):
    date_from: str
    date_to: str
    revenue: str | None = None
    expenses: str | None = None
    profit: str | None = None
    margin: str | None = None


class ProfitStructure(BaseModel):
    date_from: str
    date_to: str
    scope: str  # store — весь магазин (как P&L), products — выбранные товары или категория
    has_data: bool
    revenue: str | None
    expenses: str | None  # = выручка − прибыль до налога
    profit: str | None  # прибыль до налога
    margin: str | None
    missing_cost: int  # товаров с продажами без себестоимости — расходы неполные
    net: str | None  # прибыль после налога и НДС — магазин целиком, налоговый профиль задан (P&L)
    groups: list[ProfitGroup]
    previous: ProfitPrevious | None


class ProfitGroupProduct(BaseModel):
    id: str | None
    offer_id: str
    name: str
    amount: str
    revenue: str
    share: str | None  # доля выручки товара, %


class ProfitGroupDetail(BaseModel):
    key: str
    title: str
    products: list[ProfitGroupProduct]
    unallocated: str | None  # начисления группы без товара — только по магазину целиком


class ProfitLineReport(BaseModel):
    id: str
    filename: str
    period: str


class ProfitLine(BaseModel):
    day: str
    type_name: str
    group: str
    amount: str  # как в начислениях: + доход, − расход
    quantity: int | None
    posting_number: str | None  # ID начисления / номер отправления Ozon
    via_posting: bool  # товар определён по номеру отправления
    source: str | None  # api | xlsx
    report: ProfitLineReport | None


class ProfitLines(BaseModel):
    offer_id: str
    date_from: str
    date_to: str
    total: str  # = «начислено Ozon» товара в «Прибыли по товарам»
    count: int  # строк всего (в lines — до 500)
    lines: list[ProfitLine]
