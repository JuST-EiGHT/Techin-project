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
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INCOME, EXPENSE = "income", "expense"
SOURCE_API, SOURCE_XLSX = "api", "xlsx"


class AccrualType(Base):
    """Справочник типов начислений Ozon (/v1/finance/accrual/types) — общий для всех пользователей."""

    __tablename__ = "accrual_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # type_id Ozon
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Ozon категорию не отдаёт: ставим по знаку первой ненулевой суммы этого типа (доход > 0, расход < 0)
    category: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Accrual(Base):
    """Строка начисления: один тип начисления по отправлению/товару на дату.

    Защита от дублей: строки API пишутся «областями» (sync_scope) — все начисления одного
    отправления (`posting:<номер>`) или все начисления дня без отправления (`day:<дата>`).
    При повторной синхронизации область целиком заменяется; уникальный индекс
    (user_id, source, sync_scope, line_no) не даёт записать её дважды.
    """

    __tablename__ = "accruals"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "sync_scope", "line_no", name="uq_accruals_scope_line"),
        Index("ix_accruals_user_date", "user_id", "accrual_date"),
        Index("ix_accruals_user_posting", "user_id", "posting_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    posting_number: Mapped[str | None] = mapped_column(String(64))
    sku: Mapped[int | None] = mapped_column(BigInteger)
    accrual_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("accrual_types.id"), nullable=False)
    accrual_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # + доход, − расход
    quantity: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # api | xlsx
    sync_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AccrualDay(Base):
    """День, за который начисления из API уже загружены (для get_missing_periods)."""

    __tablename__ = "accrual_days"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entries: Mapped[int] = mapped_column(Integer, nullable=False)  # записей Ozon за день (by-day)
