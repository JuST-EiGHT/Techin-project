import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Invite(Base):
    """Приглашение на регистрацию (шаг 8.4, REGISTRATION_MODE=invite). Выдаёт администратор командой
    `python -m app.admin invite`; код показывается один раз, хранится только его хэш (sha256).

    Записи не удаляются: использование — `used_at` и кто зарегистрировался, отзыв — `revoked_at`.
    """

    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_tail: Mapped[str] = mapped_column(String(4), nullable=False)  # последние 4 знака кода — узнать в списке
    email: Mapped[str | None] = mapped_column(String(320))  # задан — зарегистрироваться можно только с этим адресом
    note: Mapped[str | None] = mapped_column(String(200))  # для кого (видит только администратор)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
