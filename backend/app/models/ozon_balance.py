import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OzonBalanceDay(Base):
    """Баланс у Ozon за день (шаг 3.6) — снимок ответа `/v1/finance/balance` за один день.

    Исходные данные (правило 12): день перезагружается, но новая запись появляется только если цифры
    изменились; актуальная — последняя по synced_at. Выплаты с датами — производные: дни, где выплачено ≠ 0.
    Суммы — как отдаёт Ozon (знак выплат не меняем), выплата для пользователя — по модулю.
    """

    __tablename__ = "ozon_balance_days"
    __table_args__ = (Index("ix_ozon_balance_days_user_day", "user_id", "day", "synced_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    opening: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # на начало дня
    accrued: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # начислено за день
    payments: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # выплачено за день (сумма payments[])
    closing: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # на конец дня
    sales: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # cashflows.sales.amount
    returns: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # cashflows.returns.amount
    services: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # сумма cashflows.services[]
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


MODES = ("auto", "standard", "daily", "weekly", "custom")


class OzonPayoutSettings(Base):
    """График выплат Ozon (шаг 3.6) — настройка с историей: изменение — новая запись с effective_from.

    auto — определять по фактическим выплатам (по умолчанию; нет истории — стандартный график из
    app/data/ozon_payouts.json); standard — стандартный график Ozon; daily — каждый рабочий день;
    weekly — раз в неделю в weekday; custom — свой день, лаг и комиссия.
    """

    __tablename__ = "ozon_payout_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer)  # 0 — понедельник
    lag_weeks: Mapped[int | None] = mapped_column(Integer)  # за какую неделю продаж: N недель назад
    fee_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # комиссия Ozon за гибкий график, %
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
