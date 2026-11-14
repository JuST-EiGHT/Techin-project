"""Расчёты с поставщиком (шаг 10.29): записи, из которых складывается кредиторская задолженность.

Правило 12: запись не перезаписывается. Исправление — новая версия (`entry_id` тот же, `version` + 1, прежняя —
`superseded_at`), удаление — `deleted_at`. Действует строка без обеих отметок.

Виды:
- opening — «долг на начало учёта»: с этого дня сервис считает долг сам (`day` — начало учёта, `amount` — долг на
  начало дня, 0 — долга не было). Новая запись такого вида — новая версия того же `entry_id`;
- purchase — закупка с отсрочкой: `day` — дата закупки, `due_day` — срок оплаты, `amount` — сумма с НДС;
- payment — оплата поставщику, внесённая здесь (оплаты из «Операций» — статья «Оплата поставщику» — засчитываются
  сами, их здесь нет).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ENTRY_KINDS = ("opening", "purchase", "payment")
COMMENT_MAX = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SupplierEntry(Base):
    __tablename__ = "supplier_entries"
    __table_args__ = (Index("ix_supplier_entries_user_kind", "user_id", "kind", "day"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # ENTRY_KINDS
    day: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    due_day: Mapped[date | None] = mapped_column(Date)  # срок оплаты — только у purchase
    number: Mapped[str | None] = mapped_column(String(100))  # № накладной / УПД
    counterparty: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX))
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")  # manual | import
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
