"""Основные настройки пользователя. Строки ещё нет — значения по умолчанию."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_settings import (
    DEFAULT_TAX_BASE,
    DEFAULT_TAX_RATE,
    DEFAULT_THEME,
    DEFAULT_VAT_MULTIPLIER,
    UserSettings,
)


def defaults(user_id: uuid.UUID) -> UserSettings:
    return UserSettings(
        user_id=user_id, theme=DEFAULT_THEME, vat_multiplier=DEFAULT_VAT_MULTIPLIER,
        tax_base=DEFAULT_TAX_BASE, tax_rate=DEFAULT_TAX_RATE,
    )


async def get_settings(db: AsyncSession, user_id: uuid.UUID) -> UserSettings:
    """Настройки для чтения (в сессию не добавляются, если строки нет)."""
    return await db.get(UserSettings, user_id) or defaults(user_id)


async def update_settings(db: AsyncSession, user_id: uuid.UUID, changes: dict) -> UserSettings:
    current = await db.get(UserSettings, user_id)
    if current is None:
        current = defaults(user_id)
        db.add(current)
    for field, value in changes.items():
        setattr(current, field, value)
    await db.commit()
    return current
