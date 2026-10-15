import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Product(Base):
    """Товар из Ozon. Заполняется синхронизацией (services/sync/products.py)."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("user_id", "ozon_product_id", name="uq_products_user_product"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    ozon_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ozon_sku: Mapped[int | None] = mapped_column(BigInteger)
    offer_id: Mapped[str] = mapped_column(String(100), nullable=False)  # артикул продавца
    name: Mapped[str | None] = mapped_column(String(500))
    category_id: Mapped[int | None] = mapped_column(BigInteger)
    barcode: Mapped[str | None] = mapped_column(String(100))
    vat: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))  # доля: 0.22
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductPrice(Base):
    """Снепшот цены товара. Новая строка — только когда что-то из полей изменилось."""

    __tablename__ = "product_prices"
    __table_args__ = (Index("ix_product_prices_product_snapshot", "product_id", "snapshot_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    marketing_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    commission_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))  # комиссия FBO, %
    acquiring_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))  # как отдаёт Ozon, см. docs/OZON_API.md
    auto_action_enabled: Mapped[bool | None] = mapped_column(Boolean)
    price_index: Mapped[str | None] = mapped_column(String(32))  # цветовой индекс цены Ozon
