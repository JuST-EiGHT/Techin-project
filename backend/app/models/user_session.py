import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# почему сеанс завершён: вышли сами, завершили на странице «Профиль и безопасность» (этот или «все, кроме этого»),
# сменили пароль, аккаунт удалён
LOGOUT, ENDED, ENDED_ALL, PASSWORD = "logout", "ended", "ended_all", "password"


class UserSession(Base):
    """Сеанс входа (шаг 10.26): вход — новая строка, обновление токена — та же строка (последняя активность, адрес).
    Токены несут номер сеанса (`sid`): завершённый сеанс не принимается ни на обновлении, ни на запросах — выйти можно с
    одного устройства или со всех. Записи не удаляются: завершённый — `ended_at` и причина; истёк — по сроку токена
    обновления от последней активности."""

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user", "user_id", "ended_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # вход
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # вход или обновление токена
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(16))
