"""Основные настройки пользователя. Строки ещё нет — значения по умолчанию."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_settings import DEFAULT_SUPPLIER_PAYMENT, DEFAULT_THEME, UserSettings


def defaults(user_id: uuid.UUID) -> UserSettings:
    return UserSettings(user_id=user_id, theme=DEFAULT_THEME, supplier_payment=DEFAULT_SUPPLIER_PAYMENT)


async def get_settings(db: AsyncSession, user_id: uuid.UUID) -> UserSettings:
    """Настройки для чтения (в сессию не добавляются, если строки нет)."""
    return await db.get(UserSettings, user_id) or defaults(user_id)


class SettingsError(Exception):
    """Настройка не подходит. Текст — для пользователя."""


def supplier_changes(current: UserSettings, changes: dict) -> dict:
    """«Откуда товар»: отсрочка нужна только у закупки с отсрочкой, у остальных вариантов — сбрасывается."""
    payment = changes.get("supplier_payment", current.supplier_payment)
    if payment != "deferred":
        return {**changes, "supplier_deferral_days": None}
    days = changes.get("supplier_deferral_days") or current.supplier_deferral_days
    if not days:
        raise SettingsError("Укажите отсрочку оплаты поставщику — через сколько дней после закупки платите")
    return {**changes, "supplier_deferral_days": days}


async def update_settings(db: AsyncSession, user_id: uuid.UUID, changes: dict) -> UserSettings:
    current = await db.get(UserSettings, user_id)
    if current is None:
        current = defaults(user_id)
        db.add(current)
    if "supplier_payment" in changes or "supplier_deferral_days" in changes:
        changes = supplier_changes(current, changes)
    for field, value in changes.items():
        setattr(current, field, value)
    await db.commit()
    return current
