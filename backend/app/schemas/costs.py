import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class CostRow(BaseModel):
    """Артикул и его действующая себестоимость (None — не задана, не 0)."""

    offer_id: str
    name: str | None
    sku: str | None
    cost_price: Decimal | None  # за единицу, без НДС, на сегодня
    vat_multiplier: Decimal | None  # None — как в налоговом профиле (НДС поставщика по умолчанию)
    effective_from: date | None  # 2000-01-01 — «с начала учёта»
    note: str | None
    updated_at: datetime | None
    history_count: int
    in_catalog: bool  # есть в товарах из Ozon API
    report_qty: int  # продано по загруженным отчётам


class CostsPage(BaseModel):
    items: list[CostRow]
    total: int
    all_count: int  # всего артикулов, без фильтров — столько строк в шаблоне «все товары»
    missing_count: int  # артикулов без себестоимости (из каталога и отчётов)


class CostUpdate(BaseModel):
    cost_price: Decimal = Field(gt=0, le=100_000_000, decimal_places=2)
    vat_multiplier: Decimal | None = Field(None, ge=1, le=2, decimal_places=3)
    note: str | None = Field(None, max_length=255)
    effective_from: date | None = Field(
        None, description="С какой даты действует; по умолчанию первая цена — с начала учёта, следующие — с сегодня"
    )


class CostSaved(BaseModel):
    item: CostRow
    created: bool  # False — такая цена с этой даты уже была
    recosted_reports: int  # в скольких отчётах пересчитана себестоимость


class CostRecord(BaseModel):
    cost_price: Decimal
    vat_multiplier: Decimal | None
    effective_from: date
    note: str | None
    source: str
    created_at: datetime


class ImportRowOut(BaseModel):
    line: int
    offer_id: str
    cost_price: Decimal | None
    cost_with_vat: Decimal | None  # в файле цена с НДС — cost_price пересчитана по ставке
    vat_multiplier: Decimal | None
    note: str | None
    effective_from: date | None
    status: Literal["new", "changed", "unchanged", "skipped", "error"]
    current_cost: Decimal | None
    message: str | None
    unknown: bool = False  # артикула нет в каталоге Ozon (10.15)
    suggestions: list[str] = []  # похожие артикулы каталога — можно привязать
    matched_from: str | None = None  # как артикул записан в файле, если сопоставлен по привязке


class ImportCounts(BaseModel):
    new: int
    changed: int
    unchanged: int
    skipped: int  # себестоимость не указана — строка пропущена (не ошибка)
    error: int


class ImportResponse(BaseModel):
    applied: bool  # False — только предпросмотр
    counts: ImportCounts
    recosted_reports: int
    rows: list[ImportRowOut]


class SelectedTemplate(BaseModel):
    """Шаблон себестоимости по выбранным товарам (10.13)."""

    product_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)
    format: Literal["xlsx", "csv"] = "xlsx"


# ── сопоставление и полнота (шаг 10.15) ──
class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=100)  # артикул как в файле
    offer_id: str = Field(min_length=1, max_length=100)  # артикул товара в Ozon


class AliasOut(BaseModel):
    id: uuid.UUID
    alias: str
    offer_id: str
    name: str | None  # название товара в Ozon
    created_at: datetime


class CoverageItem(BaseModel):
    offer_id: str
    name: str | None
    product_id: uuid.UUID | None  # в каталоге Ozon — ссылка на «Статистику товара»
    qty: int
    revenue: Decimal
    share: Decimal | None  # доля выручки периода, %


class CostCoverage(BaseModel):
    date_from: date
    date_to: date
    data_source: str  # none — за период нет начислений: продажи неизвестны
    catalog_total: int  # товаров в продаже (не архив)
    catalog_with_cost: int
    sold_total: int  # товаров с продажами за период
    sold_without_cost: int
    revenue_total: Decimal | None
    revenue_without_cost: Decimal | None
    revenue_share_without_cost: Decimal | None  # %
    items: list[CoverageItem]  # продажи без себестоимости — больше выручки первыми
