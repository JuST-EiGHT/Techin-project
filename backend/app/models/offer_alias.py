import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OfferAlias(Base):
    """Сопоставление артикула из файла с товаром Ozon (шаг 10.15): «6001 2RS» в файле поставщика → «6001-2RS» в Ozon.
    Запоминается при привязке в загрузке себестоимости — следующие файлы сопоставляются сами. «Отвязать» и
    перепривязка — removed_at у прежней записи, записи не удаляются (правило 12)."""

    __tablename__ = "offer_aliases"
    __table_args__ = (Index("ix_offer_aliases_user_key", "user_id", "alias_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)  # как в файле
    alias_key: Mapped[str] = mapped_column(String(100), nullable=False)  # engine/offer_match.normalize
    offer_id: Mapped[str] = mapped_column(String(100), nullable=False)  # артикул товара в Ozon
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
