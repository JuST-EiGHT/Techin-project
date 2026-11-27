import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReferralProgram(Base):
    """Настройки реферальной программы магазина (шаг 11.7): метки личных ссылок и ссылка на магазин. Нет строки — значения
    по умолчанию (engine/referral). Меняются на месте (как настройки), новые значения — только для новых участников:
    отправленные ссылки не переписываются."""

    __tablename__ = "referral_programs"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    campaign_name: Mapped[str] = mapped_column(String(64), nullable=False)  # utm_campaign после префикса
    utm_source: Mapped[str] = mapped_column(String(200), nullable=False)
    utm_medium: Mapped[str] = mapped_column(String(200), nullable=False)
    store_url: Mapped[str | None] = mapped_column(Text)  # страница магазина в Ozon — для ссылок «на магазин»
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ReferralParticipant(Base):
    """Участник реферальной программы: тот, кто оставил отзыв (добавляет продавец). Личная ссылка — запись истории ссылок
    (`traffic_links.link_id`) с кодом участника в utm_content. Имя, дата отзыва и заметка правятся на месте (справочник);
    удаление — `deleted_at`, ссылка остаётся (её уже могли отправить). Что происходило — `referral_events`."""

    __tablename__ = "referral_participants"
    __table_args__ = (
        Index("ix_referral_participants_code", "user_id", "code", unique=True),
        Index("ix_referral_participants_user", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)  # в ссылке: utm_content=ref_<код>
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # как подписан отзыв
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    review_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String(16), nullable=False)  # product | store — куда ведёт ссылка
    link_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # traffic_links.link_id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralEvent(Base):
    """Что происходило с участником: добавлен, ссылка отправлена (канал, заметка), отметка снята, убран, возвращён.
    Только добавление (правило 12); статус «отправлена» — по последнему событию sent / unsent."""

    __tablename__ = "referral_events"
    __table_args__ = (Index("ix_referral_events_participant", "user_id", "participant_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("referral_participants.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # created | sent | unsent | deleted | restored
    channel: Mapped[str | None] = mapped_column(String(16))  # у sent: ozon_chat | outside | review | other
    sent_on: Mapped[date | None] = mapped_column(Date)  # у sent: когда отправили (по словам продавца)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
