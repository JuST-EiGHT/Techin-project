import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Severity = Literal["yellow", "red"]
Group = Literal["sales", "price", "margin", "stock", "promo", "money", "data"]


class AnomalyProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class AnomalyItem(BaseModel):
    id: uuid.UUID
    type: str  # sales_drop, price_change … (engine/anomaly_detector)
    type_title: str  # «Падение продаж»
    group: Group | None  # sales | price | margin | stock | promo | money | data (10.5)
    severity: Severity
    title: str  # «Продажи упали на 62 %»
    message: str  # что случилось и что сделать
    link: str | None  # куда перейти: товар, акция, выплаты, календарь …
    product: AnomalyProduct | None
    metric_before: Decimal | None  # было (обычное значение) — на момент обнаружения
    metric_after: Decimal | None  # стало
    threshold: Decimal | None  # какой порог пройден
    unit: str | None  # шт./день, ₽, %, дн., шт.
    details: dict
    money_at_risk: Decimal | None = None  # деньги под риском, ₽ — оценка по последней проверке (10.5); оценить нельзя — null
    detected_at: datetime
    last_seen_at: datetime  # последняя проверка, на которой аномалия ещё была
    resolved_at: datetime | None
    resolution: str | None  # normal — вернулось в норму, escalated — стала красной (новая запись), gone — проверка не относится
    is_acknowledged: bool  # «Ок» — видел
    acknowledged_at: datetime | None


class AnomalyCounts(BaseModel):
    active: int  # активных всего
    unread: int  # активных без «Ок» — счётчик колокольчика (7.2)
    red: int  # из них красных
    yellow: int
    red_active: int  # активных красных — с «Ок» и без
    yellow_active: int


class AnomaliesPage(BaseModel):
    items: list[AnomalyItem]
    total: int
    limit: int
    offset: int
    counts: AnomalyCounts
    by_group: dict[str, int]  # сколько по группам при тех же фильтрах, кроме группы и типа (числа у фильтров)
    checked_at: datetime | None  # последняя проверка (раз в час); null — ещё не было


class AcknowledgeAll(BaseModel):
    """«Отметить все прочитанными» — по тем же фильтрам, что на странице; пусто — все активные."""

    severity: Severity | None = None
    group: Group | None = None
    type: str | None = None
    product_id: uuid.UUID | None = None


class AcknowledgedAll(BaseModel):
    acknowledged: int  # сколько отмечено
    counts: AnomalyCounts
