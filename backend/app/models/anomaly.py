import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.audit import JSONData

YELLOW, RED = "yellow", "red"
SEVERITIES = (YELLOW, RED)

# Почему закрыта: normal — метрика вернулась в норму (или событие старше окна проверки); escalated — стала красной,
# вместо неё новая запись; gone — проверка больше не относится (товар в архиве, планирование выключено, банка нет)
NORMAL, ESCALATED, GONE = "normal", "escalated", "gone"


class Anomaly(Base):
    """Аномалия (шаг 7.1): что-то пошло не так — упали продажи, изменилась цена, кончается остаток, не пришла выплата.

    Ищет детектор раз в час (engine/anomaly_detector, services/anomalies). Записи не удаляются: закрытие —
    resolved_at, повторное появление — новая запись. Активная аномалия одного типа и одного предмета (key: товар,
    товар × акция, день выплаты …) — одна: это держит частичный уникальный индекс. «Ок» пользователя —
    is_acknowledged: аномалия остаётся активной, пока метрика не вернётся в норму, но больше не просит внимания.
    Метрика до / после и порог — на момент обнаружения.
    """

    __tablename__ = "anomalies"
    __table_args__ = (
        Index("ix_anomalies_user_detected", "user_id", "detected_at"),
        Index("ix_anomalies_product", "product_id"),
        Index(
            "uq_anomalies_active", "user_id", "type", "key", unique=True,
            postgresql_where=text("resolved_at IS NULL"), sqlite_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # sales_drop, price_change … — engine/anomaly_detector
    key: Mapped[str] = mapped_column(String(120), nullable=False)  # предмет: shop, product:<id>, product:<id>:promo:<id> …
    severity: Mapped[str] = mapped_column(String(8), nullable=False)  # yellow | red
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("warehouses.id", ondelete="SET NULL"))
    metric_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))  # было (обычное значение)
    metric_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))  # стало
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))  # какой порог пройден
    details: Mapped[dict | None] = mapped_column(JSONData)  # подробности для текста: акция, даты, штуки …
    # деньги под риском, ₽ (шаг 10.5): убыток в акции, недополученная выплата, стоимость лишнего запаса — где можно
    # оценить; оценка «сейчас» — обновляется каждой проверкой, пока аномалия активна
    money_at_risk: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # последняя проверка: ещё есть
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(16))  # normal | escalated | gone
    is_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
