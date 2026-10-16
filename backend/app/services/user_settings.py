"""Основные настройки пользователя. Строки ещё нет — значения по умолчанию."""
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.stocks import DEFAULT_THRESHOLDS, Thresholds
from app.engine.supply_planner import DEFAULT_LEAD_TIME_DAYS, DEFAULT_TARGET_DAYS
from app.models.user_settings import (
    DEFAULT_AD_PCT,
    DEFAULT_SUPPLIER_PAYMENT,
    DEFAULT_TARGET_MARGIN_PCT,
    DEFAULT_THEME,
    UserSettings,
)


def defaults(user_id: uuid.UUID) -> UserSettings:
    return UserSettings(user_id=user_id, theme=DEFAULT_THEME, supplier_payment=DEFAULT_SUPPLIER_PAYMENT)


async def get_settings(db: AsyncSession, user_id: uuid.UUID) -> UserSettings:
    """Настройки для чтения (в сессию не добавляются, если строки нет)."""
    return await db.get(UserSettings, user_id) or defaults(user_id)


def price_settings(s: UserSettings) -> tuple[Decimal, Decimal]:
    """Реклама, % от цены, и целевая маржа, % — для калькулятора цены (шаг 4.2); не заданы — по умолчанию."""
    ad = DEFAULT_AD_PCT if s.price_ad_pct is None else Decimal(s.price_ad_pct)
    margin = DEFAULT_TARGET_MARGIN_PCT if s.price_target_margin_pct is None else Decimal(s.price_target_margin_pct)
    return ad, margin


def stock_thresholds(s: UserSettings) -> Thresholds:
    """Пороги статуса остатка (шаг 5.1); не заданы — 7 / 14 / 60 дней."""
    d = DEFAULT_THRESHOLDS
    return Thresholds(
        critical=d.critical if s.stock_critical_days is None else s.stock_critical_days,
        low=d.low if s.stock_low_days is None else s.stock_low_days,
        excess=d.excess if s.stock_excess_days is None else s.stock_excess_days,
    )


async def load_thresholds(db: AsyncSession, user_id: uuid.UUID) -> Thresholds:
    return stock_thresholds(await get_settings(db, user_id))


def supply_params(s: UserSettings) -> tuple[int, int]:
    """Поставки (шаг 5.2): запас после поставки и дней в пути; не заданы — 45 и 7 (engine/supply_planner)."""
    target = DEFAULT_TARGET_DAYS if s.supply_target_days is None else s.supply_target_days
    lead = DEFAULT_LEAD_TIME_DAYS if s.supply_lead_time_days is None else s.supply_lead_time_days
    return target, lead


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


STOCK_FIELDS = {"stock_critical_days": "critical", "stock_low_days": "low", "stock_excess_days": "excess"}


def check_thresholds(current: UserSettings, changes: dict) -> None:
    """Пороги остатков после изменения: от 1 до 365 дней, по возрастанию. Не переданные — как сейчас."""
    now = stock_thresholds(current)
    after = Thresholds(**{attr: changes.get(field, getattr(now, attr)) for field, attr in STOCK_FIELDS.items()})
    if error := after.error():
        raise SettingsError(error)


async def update_settings(db: AsyncSession, user_id: uuid.UUID, changes: dict) -> UserSettings:
    current = await db.get(UserSettings, user_id)
    if "supplier_payment" in changes or "supplier_deferral_days" in changes:
        changes = supplier_changes(current or defaults(user_id), changes)
    if STOCK_FIELDS.keys() & changes.keys():
        check_thresholds(current or defaults(user_id), changes)
    if current is None:
        current = defaults(user_id)
        db.add(current)
    for field, value in changes.items():
        setattr(current, field, value)
    await db.commit()
    return current
