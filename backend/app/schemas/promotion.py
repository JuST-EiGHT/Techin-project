import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

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
    avg_margin: Decimal | None  # средняя маржа участвующих (у кого её можно посчитать)
    profitable: int  # выгодные: маржа от порога «низкой маржи» пользователя (по умолчанию 10 %, шаг 7.1)
    low_margin: int  # невыгодные: от 0 до порога
    loss: int  # убыточные: ниже 0
    no_margin: int  # маржу не посчитать: нет себестоимости, комиссии или логистики
    urgent: bool  # «СРОЧНО»: заморозка сегодня или завтра — потом список товаров не поменять
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
    margin_in_promo: Decimal | None  # %, калькулятор цены (шаг 4.2); null — нет себестоимости, комиссии или логистики
    margin_level: str | None  # negative | low | ok
    first_seen_at: datetime
    # минимальная цена для участия (10.16) — калькулятор 4.2: без прибыли и без убытка; целевая — с вашей маржой
    breakeven_price: Decimal | None = None
    target_price: Decimal | None = None
    below_breakeven: bool = False  # цена в акции (у кандидата — цена входа) ниже минимальной: каждая продажа в минус
    unit_profit: Decimal | None = None  # остаётся с 1 шт. при цене в акции, до налога на прибыль
    whole_stock_profit: Decimal | None = None  # «весь остаток» (§9): если по цене в акции уйдёт весь остаток


class PromotionProductsResponse(BaseModel):
    promotion: PromotionResponse
    margin_note: str | None
    items: list[PromotionProductResponse]


class AutoAddedItem(PromotionProductResponse):
    promotion: PromotionResponse


class AutoAddedResponse(BaseModel):
    margin_note: str | None
    items: list[AutoAddedItem]


class HistoryPromotion(BaseModel):
    id: uuid.UUID
    title: str
    action_type: str | None
    date_from: datetime | None
    date_to: datetime | None
    whole_stock: bool


class HistoryPrice(BaseModel):
    at: datetime
    action_price: Decimal | None
    stock: int | None


class HistoryItem(BaseModel):
    """Одно участие товара в акции: вход, выход, цена в акции со всеми изменениями."""

    id: uuid.UUID
    promotion: HistoryPromotion
    product: PromoProduct
    entered_at: datetime  # когда сервис увидел товар в акции (по синхронизациям)
    left_at: datetime | None  # вышел или акция закончилась; null — участвует сейчас
    status: str  # active | left | ended
    action_price: Decimal | None  # последняя цена в акции
    price: Decimal | None  # обычная цена на последнюю синхронизацию
    prices: list[HistoryPrice]  # цена и количество в акции: при входе и при каждом изменении
    added_automatically: bool
    margin_in_promo: Decimal | None  # по последней синхронизации
    result: "ParticipationResult | None" = None  # результат участия (10.16)


class ResultWindow(BaseModel):
    """Окно сравнения: дни, из них с данными, продано, выручка, прибыль (None — нет себестоимости или данных)."""

    date_from: date
    date_to: date
    days: int
    days_with_data: int
    qty: int
    revenue: Decimal
    profit: Decimal | None


class ParticipationResult(BaseModel):
    """Результат участия (10.16): «во время» против такого же периода «до»; дни без данных — пропуск."""

    during: ResultWindow
    before: ResultWindow
    base: ResultWindow | None  # «до», приведённое к дням с данными «во время» — с ним и сравниваем
    known: bool  # в обоих окнах есть дни с данными
    qty_change_pct: Decimal | None
    revenue_change_pct: Decimal | None
    profit_change: Decimal | None  # ₽ за дни с данными «во время»


class PromotionResultItem(BaseModel):
    participation_id: uuid.UUID
    product: PromoProduct
    entered_at: datetime
    left_at: datetime | None
    is_current: bool  # участвует сейчас
    result: ParticipationResult


class PromotionTotals(BaseModel):
    products: int  # товаров с данными в обоих окнах
    qty: int | None
    qty_before: int | None
    revenue: Decimal | None
    revenue_before: Decimal | None
    profit: Decimal | None
    profit_before: Decimal | None
    qty_change_pct: Decimal | None
    revenue_change_pct: Decimal | None
    profit_change: Decimal | None


class PromotionResults(BaseModel):
    promotion: PromotionResponse
    totals: PromotionTotals
    items: list[PromotionResultItem]
    note: str


class HistoryResponse(BaseModel):
    date_from: date
    date_to: date
    items: list[HistoryItem]


class JoinRequest(BaseModel):
    """Без confirm — предпросмотр: ничего не меняется. Пусто — цена и количество по умолчанию (подсказаны в ответе)."""

    action_price: Decimal | None = Field(None, gt=0, le=100_000_000)
    stock: int | None = Field(None, ge=1, le=1_000_000)
    accept_loss: bool = False  # цена в минус — пользователь подтвердил, что понимает
    confirm: bool = False


class LeaveRequest(BaseModel):
    confirm: bool = False


class StateOut(BaseModel):
    participating: bool
    action_price: Decimal | None
    stock: int | None


class WholeStockTotal(BaseModel):
    qty: int  # весь остаток на складах
    profit: Decimal | None  # прибыль (убыток), если по акционной цене уйдёт весь


class ActionResponse(BaseModel):
    action: str  # join | update | leave
    applied: bool  # false — предпросмотр
    can_apply: bool
    errors: list[str]  # почему нельзя (блокируют)
    warnings: list[str]
    needs_loss_ack: bool  # цена в минус — нужно accept_loss
    before: StateOut
    after: StateOut
    action_price: Decimal | None
    stock: int | None
    stock_required: bool  # в акциях «весь остаток» количество не указывается
    min_stock: int | None
    recommended_stock: int | None
    max_action_price: Decimal | None
    price: Decimal | None  # обычная цена
    discount_pct: Decimal | None
    margin: Decimal | None  # маржа при цене в акции — калькулятор цены (шаг 4.2)
    unit_profit: Decimal | None
    min_price: Decimal | None  # наша минимальная цена
    on_hand: int
    whole_stock: bool
    whole_stock_total: WholeStockTotal | None
    boosting: bool
    message: str | None


HistoryItem.model_rebuild()
