import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_TIMEZONE = "Europe/Moscow"

# Что с сообщением (журнал отправок): pending — ждёт отправки (тихие часы, повтор после сбоя сети, лимит Telegram);
# sent — Telegram принял; failed — не отправится (бот заблокирован, чат не найден, сбой повторился несколько раз);
# outdated — не отправляли: аномалия закрылась, отмечена «Ок» или категорию выключили, пока сообщение ждало
PENDING, SENT, FAILED, OUTDATED = "pending", "sent", "failed", "outdated"
ANOMALY, TEST = "anomaly", "test"


class NotificationSettings(Base):
    """Уведомления в Telegram (шаг 7.3): к какому чату привязан аккаунт и что присылать. Нет строки — не подключено,
    настройки по умолчанию. Категории не пересекаются: «Деньги» — проверки денег (выплаты Ozon, банк, кассовый разрыв,
    операции без статьи) любой важности; «Автоакции» — Ozon сам добавил товар в акцию; остальное — по важности: красные,
    жёлтые. Тихие часы — в часовом поясе пользователя: уведомления не приходят, а уходят одним сообщением после.
    Код привязки — только хэш, живёт 10 минут, одноразовый."""

    __tablename__ = "notification_settings"
    __table_args__ = (
        Index("ix_notification_settings_chat", "chat_id"),
        Index("ix_notification_settings_code", "link_code_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)  # чат Telegram; пусто — не подключено
    telegram_username: Mapped[str | None] = mapped_column(String(64))  # @имя — показать, куда приходят
    telegram_name: Mapped[str | None] = mapped_column(String(200))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # бот не может написать (остановили)
    link_code_hash: Mapped[str | None] = mapped_column(String(64))  # sha256 шестизначного кода
    link_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_red: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notify_yellow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    notify_auto_promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notify_money: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    quiet_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    quiet_from: Mapped[int] = mapped_column(Integer, nullable=False, default=23 * 60, server_default=str(23 * 60))  # минуты
    quiet_to: Mapped[int] = mapped_column(Integer, nullable=False, default=8 * 60, server_default=str(8 * 60))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationLog(Base):
    """Журнал отправок в Telegram: что, кому, когда и чем закончилось. Одна аномалия — одно сообщение (частичный
    уникальный индекс): повторная проверка или два воркера не пришлют её дважды. Несколько ожидающих одного пользователя
    уходят одним сообщением (после тихих часов). Текст ошибки — без токена бота."""

    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notification_log_due", "status", "send_after"),
        Index("ix_notification_log_user_created", "user_id", "created_at"),
        Index(
            "uq_notification_log_anomaly", "anomaly_id", unique=True,
            postgresql_where=text("anomaly_id IS NOT NULL"), sqlite_where=text("anomaly_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    anomaly_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("anomalies.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # anomaly | test
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending | sent | failed | outdated
    chat_id: Mapped[int | None] = mapped_column(BigInteger)  # куда ушло
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    send_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # не раньше (тихие часы, повтор)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
