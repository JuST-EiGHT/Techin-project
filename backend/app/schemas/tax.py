import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

LegalForm = Literal["ip", "ooo", "self_employed"]
RegimeKey = Literal[
    "usn_income", "usn_income_expense", "osn", "ausn_income", "ausn_income_expense", "npd", "psn", "eshn", "custom"
]
VatMode = Literal["none", "special", "general"]


class TaxProfileIn(BaseModel):
    legal_form: LegalForm | None = None
    regime: RegimeKey
    rate_pct: Decimal | None = Field(None, ge=0, le=100, decimal_places=2, description="Пусто — стандартная ставка")
    custom_base: Literal["profit", "revenue"] | None = None
    vat_mode: VatMode = "none"
    vat_rate_pct: Decimal = Field(Decimal(0), ge=0, le=100)
    supplier_vat_pct: Decimal = Field(Decimal(22), ge=0, le=100, description="НДС в цене поставщика по умолчанию, %")
    contributions_year: Decimal | None = Field(None, ge=0, le=100_000_000, decimal_places=2)
    has_employees: bool = False
    patent_cost_year: Decimal | None = Field(None, ge=0, le=100_000_000, decimal_places=2)
    effective_from: date | None = Field(
        None, description="С какой даты; по умолчанию первый профиль — с начала учёта, следующие — с сегодня"
    )
    past_payments: Literal["recalc", "keep"] | None = Field(
        None,
        description="Профиль задним числом меняет прошлые налоговые платежи (POST /tax-profile/past-payments): "
        "recalc — пересчитать по новым условиям, keep — оставить как были. Оплаченные не меняются в любом случае",
    )


class TaxProfileOut(BaseModel):
    id: uuid.UUID
    effective_from: date  # 2000-01-01 — «с начала учёта»
    legal_form: LegalForm | None
    regime: RegimeKey
    rate_pct: Decimal | None
    custom_base: Literal["profit", "revenue"] | None
    vat_mode: VatMode
    vat_rate_pct: Decimal
    supplier_vat_pct: Decimal
    contributions_year: Decimal | None
    has_employees: bool
    patent_cost_year: Decimal | None
    created_at: datetime
    title: str  # коротко: «ИП · УСН «Доходы» 6 % · без НДС»


class TaxProfileState(BaseModel):
    current: TaxProfileOut | None  # действует сегодня; None — налог «не задан»
    history: list[TaxProfileOut]  # новые сверху
    created: bool | None = None  # после сохранения: False — такой профиль с этой даты уже был


class RegimeOption(BaseModel):
    key: RegimeKey
    title: str
    summary: str
    forms: list[LegalForm]
    vat_modes: list[VatMode]
    default_rates: dict[str, Decimal | None]  # форма → стандартная ставка, %; у ОСН у ИП и ООО разные
    min_rate: Decimal | None
    restriction: str | None  # когда режим продавцу не подходит
    how: list[str]  # как считаем — со стандартной ставкой и без НДС (подсказка «?» до заполнения формы)


class TaxOptions(BaseModel):
    forms: dict[str, str]
    regimes: list[RegimeOption]
    vat_modes: dict[str, str]
    vat_rates: dict[str, list[int]]  # режим НДС → допустимые ставки
    supplier_vat_rates: list[int]
    supplier_default: int
    limits: dict[str, int]


class TaxExample(BaseModel):
    title: str
    income: Decimal
    ozon_expenses: Decimal
    cost_sales: Decimal
    cost_with_vat: Decimal
    tax: Decimal
    vat: Decimal
    total: Decimal
    lines: list[str]
    warnings: list[str]


class TaxPreview(BaseModel):
    title: str
    how: list[str]  # как считаем при этом профиле — для подсказки «?»
    example: TaxExample  # на понятных цифрах: продажа на 10 000 ₽
    report_example: TaxExample | None  # на последнем загруженном отчёте
