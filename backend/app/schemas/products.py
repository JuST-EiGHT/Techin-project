import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ProductPromo(BaseModel):
    """Текущая акция, в которой товар участвует."""

    id: uuid.UUID
    title: str
    action_price: Decimal | None
    margin_in_promo: Decimal | None  # %, простая формула (engine/promotions.py)
    added_automatically: bool
    date_to: datetime | None


class ProductItem(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None
    ozon_sku: int | None
    ozon_product_id: int
    is_archived: bool
    price: Decimal | None  # цена продавца; 0 у Ozon («не задана») — null
    old_price: Decimal | None  # цена до скидки (зачёркнутая)
    marketing_price: Decimal | None  # цена с акциями продавца
    min_price: Decimal | None  # минимальная цена в Ozon (для автоакций); не задана — null
    commission_pct: Decimal | None  # комиссия FBO, %
    commission: Decimal | None  # она же в рублях от текущей цены
    acquiring_pct: Decimal | None
    price_updated_at: datetime | None  # когда цена или комиссия последний раз менялись
    cost_price: Decimal | None  # себестоимость на сегодня, без НДС; нет — null (правило 5)
    cost_with_vat: Decimal | None  # с НДС поставщика: ставка из себестоимости или налогового профиля
    stock: int | None  # доступно к продаже на всех складах; остатки не загружались — null
    in_transit: int | None  # в поставках в пути (FBO)
    avg_daily_sales: Decimal | None  # продажи в день за 30 дней; мало данных — null
    days_of_supply: Decimal | None
    stock_status: str | None  # critical | low | normal | excess | null — нет данных
    unit_profit: Decimal | None  # остаётся с 1 шт. при текущей цене (простая формула)
    margin: Decimal | None  # %, та же формула
    margin_level: str | None  # negative | low | ok
    price_index: str | None  # super | green | yellow | red | null — индекса нет
    promotions: list[ProductPromo]
    last_sale: date | None  # последний день с продажей по загруженным начислениям
    # за выбранный период (10.13) — «Прибыль по товарам»: нет начислений за период — null, без продаж — 0
    sold_qty: int | None
    revenue: Decimal | None
    period_profit: Decimal | None  # до налога; нет себестоимости — null
    flags: list["ProductFlag"] = []  # индикаторы проблем: значок с подсказкой


class ProductFlag(BaseModel):
    code: str  # loss | low_margin | low_stock | no_cost | price_changed
    text: str


class ProductsPeriod(BaseModel):
    date_from: date
    date_to: date
    data_source: str  # xlsx | api | mixed | none
    days_total: int
    missing_days: int


class ProductIds(BaseModel):
    ids: list[uuid.UUID]  # все товары по поиску и фильтрам — «выбрать все по фильтру»
    total: int


class SelectedRequest(BaseModel):
    product_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)
    date_from: date | None = None
    date_to: date | None = None


class ProductCounts(BaseModel):
    all: int  # товаров (с учётом «архивные / в продаже»)
    no_cost: int
    in_promo: int
    critical_stock: int
    low_stock: int  # меньше 14 дней, включая критичные
    negative: int
    archived: int  # в архиве Ozon — по умолчанию скрыты


class ProductsPage(BaseModel):
    items: list[ProductItem]
    total: int  # после поиска и фильтров
    limit: int
    offset: int
    counts: ProductCounts
    sales_known: bool  # хватает данных о продажах для дней запаса
    stocks_known: bool  # остатки загружались
    sales_window_days: int
    margin_note: str  # как считаем маржу — подсказка «?»
    period: ProductsPeriod  # за какой период продажи, выручка и прибыль (10.13)


class ProductCost(BaseModel):
    """Себестоимость на сегодня — для формы в боковой панели."""

    cost_price: Decimal
    vat_multiplier: Decimal | None  # null — как в налоговом профиле
    supplier_vat: Decimal  # коэффициент, которым посчитана себестоимость с НДС
    cost_with_vat: Decimal
    effective_from: date
    note: str | None
    history_count: int


class ProductStockLine(BaseModel):
    warehouse: str
    type: str  # FBO | FBS | rFBS
    cluster_name: str | None
    present: int
    in_transit: int | None
    reserved: int | None


class PriceDay(BaseModel):
    date: date
    price: Decimal | None  # null — истории цены за этот день ещё нет
    min_price: Decimal | None
    marketing_price: Decimal | None


class PriceChange(BaseModel):
    at: datetime
    price: Decimal | None
    old_price: Decimal | None
    min_price: Decimal | None
    marketing_price: Decimal | None
    commission_pct: Decimal | None


class ProductDetail(BaseModel):
    item: ProductItem
    barcode: str | None
    category_id: int | None
    vat: Decimal | None  # НДС товара в Ozon, доля
    cost: ProductCost | None
    supplier_vat: Decimal  # НДС поставщика по умолчанию (налоговый профиль) — для подсказки в форме
    stocks: list[ProductStockLine]
    price_days: list[PriceDay]  # цена на конец каждого дня за период
    price_changes: list[PriceChange]  # снимки цены за период (новые — первыми)
    first_price_at: datetime | None  # с какого момента копится история цены
ProductItem.model_rebuild()
