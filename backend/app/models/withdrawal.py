import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

REMOVE, KEEP, UNDECIDED = "remove", "keep", "undecided"
DECISIONS = (REMOVE, KEEP, UNDECIDED)


class WithdrawalDecision(Base):
    """Решение по товару в «На вывоз» (шаг 10.17): «вывозим» или «оставить» с датой. Каждое нажатие — новая запись,
    действует последняя; «Отменить решение» — запись undecided. Записи не удаляются (правило 12)."""

    __tablename__ = "withdrawal_decisions"
    __table_args__ = (Index("ix_withdrawal_decisions_user_product", "user_id", "product_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)  # remove | keep | undecided
    qty: Mapped[int | None] = mapped_column(Integer)  # сколько было рекомендовано вывезти в момент решения
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
