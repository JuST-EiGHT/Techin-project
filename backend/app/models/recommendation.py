import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecommendationHide(Base):
    """«Скрыть» рекомендацию (шаг 10.6): на N дней (настройка магазина, по умолчанию 7). Каждое нажатие — новая запись;
    «Вернуть скрытые» — cancelled_at, записи не удаляются. Условие осталось после срока — рекомендация снова видна."""

    __tablename__ = "recommendation_hides"
    __table_args__ = (Index("ix_recommendation_hides_user_until", "user_id", "hidden_until"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)  # price:<товар>, promo:<товар>:<акция>, costs …
    hidden_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
