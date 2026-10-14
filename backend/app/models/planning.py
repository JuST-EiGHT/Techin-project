"""Планирование денег (шаг 3.8) — необязательный раздел, работает без банка.

Правило 12: исходные данные не перезаписываются.
- Начальный остаток счёта — записи `money_account_balances`: исправление — новая запись, действует последняя.
- Операция — версии одной логической операции (`op_id`): исправление — новая строка (`version` + 1), прежняя
  помечается `superseded_at`; удаление — `deleted_at` у действующей строки. Действует строка без обеих отметок.
- Счета и статьи — справочники: название меняется на месте, удаление — архив (операции ссылаются на них).
Перевод между своими счетами — две операции с общим `transfer_id` (− со счёта, + на счёт), не доход и не расход.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ACCOUNT_KINDS = ("checking", "card", "cash", "wallet", "other")  # расчётный счёт, карта, наличные, кошелёк, другое
CATEGORY_KINDS = ("income", "expense", "transfer")
OP_SOURCES = ("manual", "import", "ozon_payout", "bank")
DEFAULT_HORIZON_DAYS = 60
COMMENT_MAX = 2000  # описание операции


def _now() -> datetime:
    return datetime.now(timezone.utc)  # с микросекундами: порядок записей (последний остаток, версии) однозначен


class MoneyAccount(Base):
    __tablename__ = "money_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")  # manual | bank (3.11–3.12)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class MoneyAccountBalance(Base):
    """Начальный остаток счёта на начало дня `as_of`: операции с этого дня добавляются к нему."""

    __tablename__ = "money_account_balances"
    __table_args__ = (Index("ix_money_account_balances_account", "account_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class CashCategory(Base):
    """Статья движения денег. `code` — у статей из шаблона (по профилю бизнеса), у своих — None."""

    __tablename__ = "cash_categories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # income | expense | transfer
    code: Mapped[str | None] = mapped_column(String(32))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class MoneyOperation(Base):
    """Факт движения денег: + приход, − расход. Версии — по `op_id` (см. описание модуля)."""

    __tablename__ = "money_operations"
    __table_args__ = (
        Index("ix_money_operations_user_day", "user_id", "day"),
        Index("ix_money_operations_op", "user_id", "op_id"),
        Index("ix_money_operations_external", "user_id", "external_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    op_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("cash_categories.id", ondelete="SET NULL"))
    counterparty: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX))  # описание: в списке — первые слова, полностью — в карточке
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|import|ozon_payout|bank
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # перевод: две операции с одним transfer_id
    external_key: Mapped[str | None] = mapped_column(String(64))  # выплата Ozon: ozon_payout:<дата> — без дублей
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # исправлена новой версией
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # скрыта пользователем


class PlanningSettings(Base):
    """Настройки раздела. Строки нет — раздел не включался."""

    __tablename__ = "planning_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    setup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # мастер пройден
    ozon_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("money_accounts.id", ondelete="SET NULL")
    )  # куда приходят выплаты Ozon
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # порог остатка (обзор — шаг 3.10)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_HORIZON_DAYS)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
