import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductPriceExtras(Base):
    """Свои расходы товара для расчёта цены (правка владельца 06.11.2026): хранение и прочее на 1 шт., доп. расходы в %
    от цены — вместо «только для этого расчёта». Пусто — берётся общее значение (настройка магазина или 0). Это настройка
    товара, как реклама и маржа магазина: меняется на месте, изменения — в журнале действий."""

    __tablename__ = "product_price_extras"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_product_price_extras"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    storage: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # хранение на 1 шт., ₽
    other: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # прочие расходы на 1 шт., ₽ (упаковка, маркировка…)
    extra_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # доп. расходы, % от цены; пусто — как у магазина
    note: Mapped[str | None] = mapped_column(String(200))  # что входит — для себя
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
