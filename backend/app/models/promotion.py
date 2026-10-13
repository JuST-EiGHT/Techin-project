import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Promotion(Base):
    """Акция Ozon (/v1/actions) для пользователя."""

    __tablename__ = "promotions"
    __table_args__ = (UniqueConstraint("user_id", "ozon_action_id", name="uq_promotions_user_action"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    ozon_action_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    action_type: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    date_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discount_type: Mapped[str | None] = mapped_column(String(32))
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # после этой даты нельзя повышать цены, менять список товаров и уменьшать количество
    freeze_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_add_dates: Mapped[list | None] = mapped_column(JSON)  # когда Ozon сам добавит товары
    is_participating: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    participating_products_count: Mapped[int | None] = mapped_column(Integer)
    potential_products_count: Mapped[int | None] = mapped_column(Integer)
    # «Эластичный бустинг» и «Скидка на сток»: с 6.10.2026 участвует весь остаток (BUSINESS_LOGIC §9)
    whole_stock: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PromotionProduct(Base):
    """Товар в акции: участвует (is_active) или может участвовать (кандидат)."""

    __tablename__ = "promotion_products"
    __table_args__ = (UniqueConstraint("promotion_id", "product_id", name="uq_promotion_products_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    promotion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("promotions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    action_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # цена в акции
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # цена без акции
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # min_seller_price Ozon, иначе мин. цена товара
    max_action_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # максимальная цена для участия
    stock: Mapped[int | None] = mapped_column(Integer)  # остаток, заявленный в акцию
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)  # участвует (а не кандидат)
    added_automatically: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    add_mode: Mapped[str | None] = mapped_column(String(32))  # как отдал Ozon: SELLER, MANUAL, NOT_SET…
    margin_in_promo: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))  # %, простая формула (engine/promotions.py)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
