import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Severity = Literal["yellow", "red"]
Group = Literal["sales", "price", "margin", "stock", "promo", "money"]


class AnomalyProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class AnomalyItem(BaseModel):
    id: uuid.UUID
    type: str  # sales_drop, price_change … (engine/anomaly_detector)
    type_title: str  # «Падение продаж»
    group: Group | None  # sales | price | margin | stock | promo | money
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


class AnomaliesPage(BaseModel):
    items: list[AnomalyItem]
    total: int
    limit: int
    offset: int
    counts: AnomalyCounts
    checked_at: datetime | None  # последняя проверка (раз в час); null — ещё не было
