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
    vat_multiplier: Decimal | None  # None — как в «Основных настройках»
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
