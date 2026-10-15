import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FBO, FBS, RFBS = "FBO", "FBS", "rFBS"


class Warehouse(Base):
    """Склад Ozon (FBO) или склад продавца (FBS/rFBS). Заполняется синхронизацией остатков."""

    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("user_id", "ozon_warehouse_id", name="uq_warehouses_user_warehouse"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    ozon_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)  # FBO | FBS | rFBS
    cluster_id: Mapped[int | None] = mapped_column(BigInteger)  # кластер Ozon — только у FBO
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    # FBO: склад есть в последней выгрузке остатков; FBS/rFBS: статус Ozon «created» (активный)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductStock(Base):
    """Снепшот остатка товара на складе. Новая строка — только когда остаток изменился."""

    __tablename__ = "product_stocks"
    __table_args__ = (Index("ix_product_stocks_pair_snapshot", "product_id", "warehouse_id", "snapshot_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    present: Mapped[int] = mapped_column(Integer, nullable=False)  # доступно к продаже
    reserved: Mapped[int | None] = mapped_column(Integer)  # для FBO Ozon не отдаёт
    in_transit: Mapped[int | None] = mapped_column(Integer)  # в поставках в пути (FBO)
