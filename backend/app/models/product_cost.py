import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# «С начала учёта»: первая цена артикула без даты действует и для всех прошлых отчётов
BEGINNING = date(2000, 1, 1)


class ProductCost(Base):
    """Себестоимость артикула (без НДС) с историей: новая цена — новая запись, старые остаются,
    поэтому прошлые P&L не меняются (правило 12). Связь с товаром — по offer_id (артикулу).

    Цена на дату D — последняя запись с effective_from ≤ D (при равных датах — позже созданная);
    для дат раньше первой записи — первая запись (другой информации о цене нет).
    """

    __tablename__ = "product_costs"
    __table_args__ = (Index("ix_product_costs_user_offer", "user_id", "offer_id", "effective_from"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # за единицу, без НДС
    # коэффициент НДС к себестоимости; None — как в налоговом профиле пользователя (шаг 2.5)
    vat_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual | import
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
