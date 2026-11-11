import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PLACEMENT = "placement_by_products"  # ozon_documents.doc_type отчёта «Стоимость размещения по товарам»


class StorageCost(Base):
    """Стоимость размещения товара по отчёту Ozon «Стоимость размещения по товарам» (шаг 4.2a).

    Строки — разбор файла документа (`ozon_documents`, файл хранится — правило 12): производные, их можно пересчитать из
    файла. По ним хранение магазина за день («Размещение на складе» — одна строка без товара) делится между товарами в
    прибыли по товарам и расчёте цены. За день действует последний разобранный отчёт, который его покрывает.
    День пустой — в отчёте нет дат: сумма за весь период отчёта.
    """

    __tablename__ = "storage_costs"
    __table_args__ = (Index("ix_storage_costs_user_day", "user_id", "day"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ozon_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day: Mapped[date | None] = mapped_column(Date)
    sku: Mapped[int | None] = mapped_column(BigInteger)
    offer_id: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # стоимость размещения, ₽ (вес доли)
