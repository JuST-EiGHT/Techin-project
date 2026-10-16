import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

FBO, FBS = "FBO", "FBS"  # FBS — и rFBS: их отдаёт тот же список Ozon, тип склада — в warehouses


class Posting(Base):
    """Отправление (заказ) Ozon — для продаж по кластерам и складам (шаг 5.2, планирование поставок).

    FBO — склад Ozon, откуда отгрузили (`warehouse_id`), и кластер покупателя (`cluster_to`); FBS / rFBS — склад
    продавца. Строка не удаляется; статус — текущий у Ozon (доставлено, отменено), меняется при синхронизации.
    Персональных данных покупателя не храним: ни имени, ни телефона, ни адреса (`raw_json` — без них).
    """

    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_number", name="uq_postings_user_number"),
        Index("ix_postings_user_scheme_day", "user_id", "scheme", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scheme: Mapped[str] = mapped_column(String(4), nullable=False)  # FBO | FBS
    posting_number: Mapped[str] = mapped_column(String(64), nullable=False)
    order_number: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    # когда заказали: FBO — created_at, FBS — in_process_at
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # день заказа по Москве
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger)  # id склада Ozon (FBO) или склада продавца (FBS)
    warehouse_name: Mapped[str | None] = mapped_column(String(255))
    cluster_from: Mapped[str | None] = mapped_column(String(255))  # кластер склада отгрузки
    cluster_to: Mapped[str | None] = mapped_column(String(255))  # кластер покупателя
    raw_json: Mapped[dict | None] = mapped_column(JSON)  # ответ Ozon без персональных данных
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    items: Mapped[list["PostingItem"]] = relationship(
        back_populates="posting", cascade="all, delete-orphan", lazy="selectin", order_by="PostingItem.sku"
    )


class PostingItem(Base):
    """Товар в отправлении: SKU, артикул, штуки, цена."""

    __tablename__ = "posting_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    posting_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("postings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sku: Mapped[int | None] = mapped_column(BigInteger)
    offer_id: Mapped[str | None] = mapped_column(String(100))
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    posting: Mapped[Posting] = relationship(back_populates="items")


class PostingSync(Base):
    """С какого дня отправления загружены и когда — по схеме: без этого «нет заказов» не отличить от «не загружали»."""

    __tablename__ = "posting_syncs"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    scheme: Mapped[str] = mapped_column(String(4), primary_key=True)
    loaded_from: Mapped[date] = mapped_column(Date, nullable=False)  # с этого дня (по Москве) отправления есть
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # до этого момента
