"""Отчёт Ozon (XLSX) после разбора — одинаковый вид для обоих типов отчётов.

Суммы — в рублях, Decimal (в JSON — строкой). Себестоимость — без НДС.
"""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

ReportType = Literal["accrual", "realization"]


class ArticleSales(BaseModel):
    name: str
    sku: str
    qty: int
    revenue: Decimal  # начисления: сумма строк «Выручка»; реализация: «Реализовано на сумму»
    cost: Decimal | None  # себестоимость единицы; None — нет в справочнике (не 0, правило 5)
    total_cost: Decimal | None  # cost × qty
    has_cost: bool


class MissingCost(BaseModel):
    article: str
    sku: str
    name: str
    qty: int


class NormalizedReportData(BaseModel):
    report_type: ReportType
    period: str = Field(description="Период как в отчёте, например «07.09.2026-13.09.2026»")
    period_start: date | None
    period_end: date | None
    auto_total: Decimal = Field(description="Сколько придёт от Ozon по отчёту")
    revenue_base: Decimal = Field(description="База для процентов и маржи")
    cost_sales: Decimal = Field(description="Себестоимость проданного без НДС (только известная)")
    by_type: dict[str, Decimal]
    by_article: dict[str, ArticleSales]
    missing_cost: list[MissingCost]
    warnings: list[str]
    sheet_name: str
    header_row: int = Field(description="Строка заголовков таблицы в Excel (с 1)")
    rows_count: int = Field(description="Строк с товарами: «Выручка» в начислениях, строки таблицы в реализации")
