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
    """Товар в акции: участие (is_active) или кандидат — может участвовать.

    История (шаг 4.3, правило 12): участие не удаляется — выход из акции ставит left_at, повторный вход — новая
    строка; смена акционной цены или количества — снимок `promotion_price_snapshots`. Кандидаты — текущее
    состояние Ozon, заменяются при синхронизации. Текущая строка пары товар × акция — с left_at = NULL, она одна.
    """

    __tablename__ = "promotion_products"
    __table_args__ = (
        Index(
            "uq_promotion_products_current", "promotion_id", "product_id", unique=True,
            postgresql_where=text("left_at IS NULL"), sqlite_where=text("left_at IS NULL"),
        ),
    )

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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # вошёл в акцию
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # вышел; NULL — участвует сейчас
    min_stock: Mapped[int | None] = mapped_column(Integer)  # кандидат: сколько штук Ozon требует минимум
    recommended_stock: Mapped[int | None] = mapped_column(Integer)  # кандидат: сколько Ozon советует заявить
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionPriceSnapshot(Base):
    """Цена и количество товара в акции: снимок при входе и при каждом изменении (как цены в шаге 1.2)."""

    __tablename__ = "promotion_price_snapshots"
    __table_args__ = (Index("ix_promotion_price_snapshots_row", "promotion_product_id", "snapshot_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    promotion_product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("promotion_products.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    stock: Mapped[int | None] = mapped_column(Integer)
