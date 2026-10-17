import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Кто меняет: вручную один товар (6.2), вручную списком (6.3), правило (фаза 9)
MANUAL_SINGLE, MANUAL_BULK, AUTO_RULE = "manual_single", "manual_bulk", "auto_rule"
CHANGE_TYPES = (MANUAL_SINGLE, MANUAL_BULK, AUTO_RULE)

# Итог: preview — предпросмотр (в Ozon не ходили); applied — Ozon применил; blocked — не прошло проверки
# безопасности (§8); rejected — Ozon отказал по товару; failed — ошибка запроса, ничего не изменилось;
# unknown — Ozon не ответил, могло примениться (для паузы считается применённым)
PREVIEW, APPLIED, BLOCKED, REJECTED, FAILED, UNKNOWN = "preview", "applied", "blocked", "rejected", "failed", "unknown"
STATUSES = (PREVIEW, APPLIED, BLOCKED, REJECTED, FAILED, UNKNOWN)


class PriceChange(Base):
    """Изменение цены (или минимальной цены) товара в Ozon через сервис — с шага 6.2; таблица — шаг 6.1.

    Цены «до» — что было в Ozon по последним данным в момент изменения, «после» — что отправили; не меняющееся поле —
    пусто. is_dry_run / is_applied — быстрые признаки для отбора, точный итог — status. Предпросмотр — запись со
    status preview (живёт 15 минут), применение — новая запись с preview_id. Откат (6.4) — новая запись с
    rollback_of_id. Подробности запроса и ответа Ozon — в audit_log (entity_type product_price, entity_id — id
    этой записи).
    """

    __tablename__ = "price_changes"
    __table_args__ = (
        Index("ix_price_changes_user_created", "user_id", "created_at"),
        Index("ix_price_changes_product_created", "product_id", "created_at"),
        # один предпросмотр — одна попытка применения (защита от двойного нажатия и повтора запроса)
        Index(
            "uq_price_changes_preview", "preview_id", unique=True,
            postgresql_where=text("preview_id IS NOT NULL"), sqlite_where=text("preview_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(100), nullable=False)  # артикул на момент изменения
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)  # manual_single | manual_bulk | auto_rule
    reason: Mapped[str | None] = mapped_column(Text)  # своя причина, «Откат изменения от …», название правила
    price_before: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_after: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    min_price_before: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    min_price_after: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    is_dry_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    errors: Mapped[list | None] = mapped_column(JSON)  # что заблокировало (проверки §8)
    warnings: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)  # отказ или ошибка Ozon
    rollback_of_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("price_changes.id"))
    preview_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("price_changes.id"))  # попытка ← предпросмотр
