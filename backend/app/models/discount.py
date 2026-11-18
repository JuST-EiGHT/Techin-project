"""Заявки покупателей на скидку (шаг 4.3a, `/v2/actions/discounts-task/list`) и решения по ним через сервис.

Правило 12: заявка — строка навсегда (последний статус и решение Ozon — здесь же, для списка), каждая смена статуса
или решения — новая запись `discount_task_events`; предпросмотр решения и попытка его применить — свои записи
`discount_decisions` (один предпросмотр — одна попытка, как цены 6.2). Ничего не удаляется и не перезаписывается,
кроме «последнего состояния» заявки, которое повторяет последнее событие.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Статусы Ozon (v2): новая, одобрена, отклонена. «Истекла» — не статус Ozon: новая, у которой прошёл end_at (считаем)
NEW, APPROVED, DECLINED = "NEW", "APPROVED", "DECLINED"
OZON_STATUSES = (NEW, APPROVED, DECLINED)

# Откуда событие: увидели при синхронизации или записали после своего решения (ответ Ozon «принято»)
FROM_OZON, FROM_SERVICE = "ozon", "service"

# Решение: одобрить или отклонить
APPROVE, DECLINE = "approve", "decline"
# Итог решения — как у цен (models/price_change): preview — предпросмотр; applied — Ozon принял; blocked — не прошло
# наши проверки; rejected — Ozon отказал по заявке; failed — ошибка запроса (ничего не изменилось); unknown — Ozon не
# ответил (могло примениться)
PREVIEW, APPLIED, BLOCKED, REJECTED, FAILED, UNKNOWN = "preview", "applied", "blocked", "rejected", "failed", "unknown"


class DiscountTask(Base):
    """Заявка на скидку. Поля — как в ответе Ozon (OZON_API.md «Заявки на скидку»); сотрудника продавца (имя, e-mail)
    не храним — только признак «обработал сотрудник» и `is_auto_moderated`."""

    __tablename__ = "discount_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "ozon_task_id", name="uq_discount_tasks_user_task"),
        Index("ix_discount_tasks_user_created", "user_id", "created_at_ozon"),
        Index("ix_discount_tasks_product", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ozon_task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku: Mapped[int | None] = mapped_column(BigInteger)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    name: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # последний статус Ozon: NEW | APPROVED | DECLINED
    created_at_ozon: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # окончание действия заявки
    edited_till: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # до когда можно изменить решение
    requested_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    requested_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    requested_quantity_max: Mapped[int | None] = mapped_column(Integer)
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # до всех скидок
    min_auto_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # минимальная цена после автоприменения
    reduction_factor: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    approved_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    approved_discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # ₽
    approved_quantity_max: Mapped[int | None] = mapped_column(Integer)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_auto_moderated: Mapped[bool | None] = mapped_column(Boolean)
    auto_moderated_info: Mapped[dict | None] = mapped_column(JSON)  # min_price, max_price, min_percent, max_percent
    raw_json: Mapped[dict | None] = mapped_column(JSON)  # ответ Ozon без данных сотрудника
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # когда последний раз видели


class DiscountTaskEvent(Base):
    """Смена статуса или решения по заявке (только добавление). Первое событие — заявка появилась."""

    __tablename__ = "discount_task_events"
    __table_args__ = (Index("ix_discount_task_events_task", "task_id", "seen_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("discount_tasks.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    approved_quantity_max: Mapped[int | None] = mapped_column(Integer)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # по Ozon
    is_auto_moderated: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # ozon | service
    decision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("discount_decisions.id"))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiscountDecision(Base):
    """Решение по заявке через сервис: предпросмотр (status preview, живёт 15 минут) или попытка его применить (новая
    запись с preview_id — уникально: двойное нажатие не отправит решение дважды). Решение списком — общий batch_id у
    предпросмотров. Запрос и ответ Ozon — в audit_log (entity_type discount_task, entity_id — id заявки Ozon)."""

    __tablename__ = "discount_decisions"
    __table_args__ = (
        Index("ix_discount_decisions_user_created", "user_id", "created_at"),
        Index("ix_discount_decisions_task", "task_id", "created_at"),
        Index("ix_discount_decisions_batch", "batch_id"),
        Index(
            "uq_discount_decisions_preview", "preview_id", unique=True,
            postgresql_where=text("preview_id IS NOT NULL"), sqlite_where=text("preview_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("discount_tasks.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # approve | decline
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    preview_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("discount_decisions.id"))
    approved_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    approved_quantity_max: Mapped[int | None] = mapped_column(Integer)
    seller_comment: Mapped[str | None] = mapped_column(Text)
    # расчёт на момент решения (калькулятор 4.2): маржа и прибыль с 1 шт. при цене решения, минимальная для участия
    margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    unit_profit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    breakeven_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    accept_loss: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    errors: Mapped[list | None] = mapped_column(JSON)
    warnings: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)  # отказ или ошибка Ozon
