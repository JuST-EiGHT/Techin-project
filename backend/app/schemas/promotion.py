import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

WHOLE_STOCK_WARNING = (
    "С 6 октября 2026 года в этой акции участвует весь остаток товара — ограничить количество нельзя. "
    "По акционной цене может уйти весь сток."
)
NO_COST_NOTE = (
    "Маржа считается калькулятором цены: нужны себестоимость товара, комиссия Ozon и данные о логистике (хотя бы одна "
    "продажа в магазине за 90 дней). У товаров без них маржи пока нет."
)


class PromotionResponse(BaseModel):
    id: uuid.UUID
    ozon_action_id: int
    title: str
    action_type: str | None
    date_from: datetime | None
    date_to: datetime | None
    discount_type: str | None
    discount_value: Decimal | None
    freeze_date: datetime | None
    is_frozen: bool  # после freeze_date нельзя повышать цены и менять список товаров
    auto_add_dates: list[str] | None
    is_participating: bool
    participating_products_count: int | None
    potential_products_count: int | None
    whole_stock: bool
    whole_stock_warning: str | None
    products_active: int  # наших товаров участвует
    candidates: int  # наших товаров может участвовать
    auto_added: int  # из них добавлены автоматически
    min_margin: Decimal | None  # минимальная маржа среди участвующих
    synced_at: datetime


class PromoProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None
    ozon_sku: int | None


class PromotionProductResponse(BaseModel):
    product: PromoProduct
    action_price: Decimal | None
    price: Decimal | None
    discount_pct: Decimal | None  # скидка от обычной цены, %
    min_price: Decimal | None
    max_action_price: Decimal | None
    below_min_price: bool  # акционная цена ниже минимальной
    stock: int | None  # заявлено в акцию
    stock_on_hand: int  # наш остаток на всех складах (для акций «весь остаток» — сколько может уйти)
    is_active: bool
    added_automatically: bool
    add_mode: str | None
    margin_in_promo: Decimal | None  # %, простая формула; null — нет себестоимости или комиссии
    margin_level: str | None  # negative | low | ok
    first_seen_at: datetime


class PromotionProductsResponse(BaseModel):
    promotion: PromotionResponse
    margin_note: str | None
    items: list[PromotionProductResponse]


class AutoAddedItem(PromotionProductResponse):
    promotion: PromotionResponse


class AutoAddedResponse(BaseModel):
    margin_note: str | None
    items: list[AutoAddedItem]
