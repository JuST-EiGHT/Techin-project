import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field


class CalculateRequest(BaseModel):
    """Товар и свои значения для расчёта — не переданное берётся из данных Ozon, справочника и настроек.
    Ничего не сохраняется: рекламу и целевую маржу сохраняет PATCH /settings."""

    product_id: uuid.UUID
    cost: Decimal | None = Field(None, gt=0, le=10_000_000)  # себестоимость 1 шт. без НДС
    commission_pct: Decimal | None = Field(None, ge=0, le=100)
    acquiring_pct: Decimal | None = Field(None, ge=0, le=100)
    ad_pct: Decimal | None = Field(None, ge=0, le=90)
    logistics: Decimal | None = Field(None, ge=0, le=1_000_000)
    storage: Decimal | None = Field(None, ge=0, le=1_000_000)
    other: Decimal | None = Field(None, ge=0, le=1_000_000)
    target_margin_pct: Decimal | None = Field(None, ge=0, le=90)
    price: Decimal | None = Field(None, gt=0, le=100_000_000)  # проверить другую цену
    promo_price: Decimal | None = Field(None, gt=0, le=100_000_000)
    # «а если поставить …» (10.15): маржа, прибыль и расходы при этих ценах — тем же расчётом
    what_if: list[Annotated[Decimal, Field(gt=0, le=100_000_000)]] = Field(default_factory=list, max_length=8)


class CalcInputs(BaseModel):
    cost: Decimal | None
    supplier_vat: Decimal
    cost_with_vat: Decimal | None
    commission_pct: Decimal | None
    acquiring_pct: Decimal
    ad_pct: Decimal
    logistics: Decimal | None
    logistics_source: str | None  # product | shop | manual | null — нет данных
    logistics_text: str
    storage: Decimal
    other: Decimal
    target_margin_pct: Decimal
    price: Decimal | None
    promo_price: Decimal | None
    sources: dict[str, str]  # поле → откуда значение


class CalcTax(BaseModel):
    title: str
    known: bool
    notes: list[str]
    profit_tax_pct: Decimal


class Breakdown(BaseModel):
    """Из чего складывается цена 1 шт.; расходы — положительные."""

    price: Decimal
    commission: Decimal
    acquiring: Decimal
    ad: Decimal
    logistics: Decimal
    storage: Decimal
    other: Decimal
    cost: Decimal  # себестоимость с НДС поставщика
    vat: Decimal  # НДС к уплате
    income_tax: Decimal  # налог с дохода
    profit: Decimal  # до налога на прибыль
    margin: Decimal | None
    profit_tax: Decimal
    net: Decimal  # после налога на прибыль


class CalcResult(BaseModel):
    min_price: Decimal | None
    min_error: str | None
    target_price: Decimal | None
    target_error: str | None
    profitable: bool | None  # текущая цена не ниже минимальной
    floor_price: Decimal | None = None  # цена с минимальной допустимой прибылью (настройка 10.26; не задана — null)
    floor_ok: bool | None = None  # текущая цена не ниже неё
    min_profit: Decimal | None = None  # сама настройка, ₽ с продажи
    current: Breakdown | None
    promo: Breakdown | None
    what_if: list[Breakdown] = []  # по ценам из запроса, в том же порядке


class CalcProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class CalculateResponse(BaseModel):
    product: CalcProduct
    inputs: CalcInputs
    tax: CalcTax
    missing: list[str]  # чего не хватает для расчёта — тогда result = null
    result: CalcResult | None
    promo_title: str | None  # акция, по цене которой посчитана «маржа в акции»


class PricePromo(BaseModel):
    title: str
    action_price: Decimal
    margin: Decimal | None
    count: int  # в скольких акциях товар
    added_automatically: bool


class PriceRow(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None
    ozon_sku: int | None
    is_archived: bool
    price: Decimal | None
    min_price: Decimal | None
    target_price: Decimal | None
    margin: Decimal | None
    unit_profit: Decimal | None
    below_min: bool
    below_target: bool
    gap_pct: Decimal | None  # цена выше минимальной на, %
    promo: PricePromo | None  # акция с самой низкой ценой
    logistics: Decimal | None
    logistics_source: str | None
    missing: list[str]
    errors: list[str]


class PriceCounts(BaseModel):
    all: int
    below_min: int
    below_target: int
    no_data: int
    promo_loss: int


class PriceSettingsOut(BaseModel):
    ad_pct: Decimal
    target_margin_pct: Decimal
    defaults: bool


class ShopLogistics(BaseModel):
    per_unit: Decimal | None
    text: str


class PricesPage(BaseModel):
    items: list[PriceRow]
    total: int
    limit: int
    offset: int
    counts: PriceCounts
    settings: PriceSettingsOut
    tax: CalcTax
    shop_logistics: ShopLogistics


class Suggestion(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None
    price: Decimal
    min_price: Decimal
    target_price: Decimal | None
    margin: Decimal | None
    raise_to: Decimal  # до какой цены поднять: минимальной или целевой
    raise_pct: Decimal


class Suggestions(BaseModel):
    loss: list[Suggestion]  # цена ниже минимальной — самые убыточные первыми
    loss_count: int
    underpriced: list[Suggestion]  # между минимальной и целевой — сильнее отстающие от целевой первыми
    underpriced_count: int
