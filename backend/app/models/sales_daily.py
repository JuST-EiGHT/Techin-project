import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SalesDaily(Base):
    """Продажи товара за день (шаг 3.5) — производная из начислений «Выручка» и «Возврат выручки».

    Пересобирается из начислений за затронутые дни (синхронизация финансов, загрузка и скрытие отчётов),
    руками не правится (docs/DATA_HISTORY.md). Источник дня — как в P&L: отчёт XLSX, если он есть, иначе
    Ozon API. День без загруженных начислений — строк нет и это «нет данных», а не 0 продаж: отличить
    помогает покрытие (`pnl_data.coverage`).
    """

    __tablename__ = "sales_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "day", "offer_id", name="uq_sales_daily_day_offer"),
        Index("ix_sales_daily_user_offer_day", "user_id", "offer_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    offer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    sku: Mapped[int | None] = mapped_column(BigInteger)
    qty_sold: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_returned: Mapped[int] = mapped_column(Integer, nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # «Выручка»
    returns_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # «Возврат выручки», ≥ 0
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # xlsx | api
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
