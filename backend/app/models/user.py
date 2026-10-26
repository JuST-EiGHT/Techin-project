import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """Строка users — владелец данных: всё (товары, начисления, настройки…) привязано к users.id.

    Человек (есть email и пароль) — аккаунт и его первый магазин. Его другие магазины Ozon (шаг 8.4b) — тоже строки
    users, но без входа: owner_id — аккаунт человека, store_name — название. Один магазин — один кабинет Ozon
    (ozon_client_id). Так данные разных магазинов не пересекаются, а весь код данных по-прежнему работает с одним user_id
    (текущий магазин — `services/stores.get_current_store`).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)  # у магазина — пусто
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255))  # «Имя Фамилия» — для писем и подписей (из полей ниже)
    first_name: Mapped[str | None] = mapped_column(String(100))  # шаг 10.26: имя и фамилия раздельно
    last_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(32))  # необязательно: связаться, если нет доступа к почте
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # токены, выпущенные раньше, недействительны (смена пароля — выход на всех устройствах)
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # магазин (шаг 8.4b): чей он; у аккаунта человека — пусто
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    store_name: Mapped[str | None] = mapped_column(String(100))  # название магазина (и у аккаунта — его первого)
    # кабинет Ozon магазина: задаётся первым ключом, ключи другого Client-Id сюда не принимаются — только новым магазином
    ozon_client_id: Mapped[str | None] = mapped_column(String(32))
    # удаление аккаунта (шаг 10.26): запрошено — когда, удалится — когда (отсрочка 30 дней, до неё можно отменить)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def account_id(self) -> uuid.UUID:
        """Аккаунт человека: у магазина — владелец, у аккаунта — он сам."""
        return self.owner_id or self.id
