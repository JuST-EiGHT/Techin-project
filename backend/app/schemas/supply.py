"""Планирование поставок (шаг 5.2): ответ /api/v1/supply/recommendations."""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.stock import StockProduct, Thresholds


class SupplyParams(BaseModel):
    target_days: int  # запас после поставки, дней продаж
    lead_time_days: int  # сколько дней поставка в пути


class PostingsCoverage(BaseModel):
    loaded_from: date  # отправления загружены с этого дня
    synced_at: datetime
    window_from: date  # окно расчёта: 30 дней, но не раньше загруженного
    window_to: date
    days: int
    enough: bool  # дней с данными хватает (от 7)


class SupplyDestination(BaseModel):
    key: str
    name: str  # кластер Ozon или свой склад
    warehouse_type: str | None  # FBS | rFBS — у своего склада
    warehouses: list[str]  # склады Ozon кластера, где сейчас лежит этот товар


class SupplyItem(BaseModel):
    product: StockProduct
    kind: str  # supply — поставка в кластер Ozon, purchase — закупка на свой склад
    urgency: str  # critical | urgent | planned
    destination: SupplyDestination
    sold_30: int  # заказано без отмен за окно (до 30 дней)
    sold_7: int
    avg_daily: Decimal  # продажи в день за 30 дней
    rate: Decimal  # за 7 дней
    trend: Decimal | None
    stock: int  # там сейчас
    in_transit: int
    days_left: Decimal | None  # хватит на, дней; null — за 7 дней не продавалось
    target_stock: int
    need: int  # нужно к приходу поставки
    to_order: int  # отправить / закупить
    arrives_late: bool  # кончится раньше, чем доедет поставка


class SupplyTotals(BaseModel):
    critical: int
    urgent: int
    planned: int
    supply_units: int
    purchase_units: int
    products: int


class SupplyReport(BaseModel):
    params: SupplyParams
    saved: SupplyParams
    is_saved: bool
    thresholds: Thresholds
    has_key: bool
    postings_known: bool  # отправления FBO хоть раз загружались
    fbo: PostingsCoverage | None
    fbs: PostingsCoverage | None
    has_own_warehouses: bool
    totals: SupplyTotals
    today: date
    items: list[SupplyItem]
