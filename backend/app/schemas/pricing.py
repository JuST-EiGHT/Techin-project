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
    extra_pct: Decimal | None = Field(None, ge=0, le=90)  # доп. расходы, % от цены
    returns: Decimal | None = Field(None, ge=0, le=1_000_000)  # возвраты и невыкупы на 1 шт. (4.2a)
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
    extra_pct: Decimal = Decimal(0)  # доп. расходы, % от цены: свои у товара или общие магазина
    shop_extra_pct: Decimal = Decimal(0)  # общие доп. расходы магазина, % (настройка)
    saved: "SavedExtras | None" = None  # что сохранено для этого товара
    target_margin_pct: Decimal
    price: Decimal | None
    promo_price: Decimal | None
    sources: dict[str, str]  # поле → откуда значение
    returns: Decimal = Decimal(0)  # возвраты и невыкупы на 1 шт. (4.2a)
    facts: "CalcFacts | None" = None
    for_all: "AllExtras | None" = None  # что задано для всех товаров (правка 11.11.2026)


class CalcFacts(BaseModel):
    """Факт по начислениям Ozon за окно продаж (4.2a) — для подсказок: выкуп, из чего реклама, оценки магазина."""

    days: int | None  # окно: 30 или 90 дней; None — продаж нет и у магазина
    qty: int  # продано товара за окно
    own: bool  # у товара свои продажи за окно (иначе — средние магазина)
    not_bought: int  # невыкуплено
    returned: int  # вернули после покупки
    buyout_pct: Decimal | None  # выкуп, %
    ad_own_pct: Decimal | None  # реклама товара («Звёздные товары» и др.), % от его продаж
    ad_shared_pct: Decimal | None  # реклама без товара (кампании), % от продаж магазина
    storage_by_report: bool  # хранение поделено по отчёту Ozon о размещении
    shop_storage: Decimal | None
    shop_returns: Decimal | None
    shop_ads_pct: Decimal | None


class SavedExtras(BaseModel):
    """Свои расходы товара в расчёте цены; пусто — общее значение или факт по начислениям."""

    storage: Decimal | None = None
    other: Decimal | None = None
    extra_pct: Decimal | None = None
    ad_pct: Decimal | None = None  # реклама, % — вместо факта (4.2a)
    note: str | None = None


class ExtrasUpdate(BaseModel):
    """Сохранить свои расходы товара — для всех расчётов (таблицы, акции, подсказки). Пусто — убрать своё значение."""

    storage: Decimal | None = Field(None, ge=0, le=1_000_000)
    other: Decimal | None = Field(None, ge=0, le=1_000_000)
    extra_pct: Decimal | None = Field(None, ge=0, le=90)
    ad_pct: Decimal | None = Field(None, ge=0, le=90)  # реклама, % — своя у товара (4.2a)
    note: str | None = Field(None, max_length=200)


class AllExtras(BaseModel):
    """Расходы для всех товаров (правка 11.11.2026): пусто — реклама и хранение по начислениям, прочее и доп. — 0."""

    ad_pct: Decimal | None = None
    storage: Decimal | None = None
    other: Decimal | None = None
    extra_pct: Decimal | None = None


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
    extra: Decimal = Decimal(0)  # доп. расходы продавца, ₽
    returns: Decimal = Decimal(0)  # возвраты и невыкупы на 1 шт. (4.2a)


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
    for_all: AllExtras | None = None  # для всех товаров (правка 11.11.2026)


class ShopLogistics(BaseModel):
    per_unit: Decimal | None
    text: str
    # 4.2a: средние магазина за окно — возвраты и хранение на 1 шт., реклама — % от продаж (всего и без товара)
    returns: Decimal | None = None
    storage: Decimal | None = None
    ads_pct: Decimal | None = None
    unattributed_ads_pct: Decimal | None = None
    storage_by_report: bool = False


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


CalcInputs.model_rebuild()
CalculateResponse.model_rebuild()


CalcInputs.model_rebuild()
