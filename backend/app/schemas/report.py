"""Отчёт Ozon (XLSX) после разбора — одинаковый вид для обоих типов отчётов.

Суммы — в рублях, Decimal (в JSON — строкой). Себестоимость — без НДС.
"""
import uuid
from datetime import date, datetime
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


# ---------- сохранённые отчёты (шаг 2.2) ----------


class ReportItem(BaseModel):
    id: uuid.UUID
    filename: str
    report_type: ReportType
    period: str
    period_start: date | None
    period_end: date | None
    status: Literal["processing", "ready", "error"]
    error_message: str | None  # ready — нет; error — почему не удалось перечитать файл (итоги прежние)
    uploaded_at: datetime
    parsed_at: datetime
    auto_total: Decimal  # сколько придёт от Ozon по отчёту
    manual_total: Decimal | None  # правка пользователя «сколько реально пришло»
    effective_total: Decimal  # manual_total, если задан, иначе auto_total
    revenue_base: Decimal
    cost_sales: Decimal
    vat_multiplier: Decimal
    tax_rate: Decimal
    rows_count: int
    missing_cost_count: int  # артикулов без себестоимости
    warnings_count: int


class ReportDetail(ReportItem):
    file_size: int
    summary: NormalizedReportData


class ReportsPage(BaseModel):
    items: list[ReportItem]
    total: int


class ReportUpdate(BaseModel):
    """Правки пользователя. Не переданное поле не меняется; manual_total=null — вернуться к авто,
    vat_multiplier/tax_rate=null — значения по умолчанию (1.22 и 0.20)."""

    manual_total: Decimal | None = Field(None, ge=-100_000_000, le=100_000_000, decimal_places=2)
    vat_multiplier: Decimal | None = Field(None, ge=1, le=2)
    tax_rate: Decimal | None = Field(None, ge=0, lt=1)


class ReportConflictItem(BaseModel):
    id: uuid.UUID
    filename: str
    period: str
    period_start: date | None
    period_end: date | None
    uploaded_at: datetime
    same_file: bool
