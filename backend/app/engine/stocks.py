"""Запас товара на складе: дни запаса и статус. Чистые функции — без БД и HTTP.

days_of_supply = остаток / среднесуточные продажи
статус:  < critical дней — critical, < low — low, > excess — excess, иначе normal

Пороги — настройки пользователя (шаг 5.1, «Основные настройки → Остатки»), по умолчанию 7 / 14 / 60 дней.

Нет данных о продажах (avg_daily_sales = None) → дни запаса и статус None («нет данных»),
а не 0: ноль выглядел бы как «товар кончился».
Продажи известны и равны нулю → дни запаса не определены (None), но лежащий без
продаж остаток — это excess.

Шаг 10.17 (BUSINESS_LOGIC §10, «Запасы»): дата окончания = сегодня + остаток / продажи в день (продаж нет — не
продаётся, а не дата); стоимость запаса = остаток × себестоимость с НДС (нет себестоимости — нет данных, а не 0).
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CRITICAL_DAYS = 7
LOW_DAYS = 14
EXCESS_DAYS = 60
MAX_DAYS = 365

CRITICAL, LOW, NORMAL, EXCESS = "critical", "low", "normal", "excess"
STATUSES = (CRITICAL, LOW, NORMAL, EXCESS)

_TENTH = Decimal("0.1")


@dataclass(frozen=True)
class Thresholds:
    """Пороги статуса остатка, дней запаса: меньше critical — «Критично», меньше low — «Мало», больше excess —
    «Избыток»."""

    critical: int = CRITICAL_DAYS
    low: int = LOW_DAYS
    excess: int = EXCESS_DAYS

    def error(self) -> str | None:
        """Текст для пользователя, если пороги не подходят; иначе None."""
        if not all(1 <= d <= MAX_DAYS for d in (self.critical, self.low, self.excess)):
            return f"Пороги остатков — целые числа дней от 1 до {MAX_DAYS}"
        if not self.critical < self.low < self.excess:
            return "Пороги остатков идут по возрастанию: «Критично» меньше «Мало», «Мало» меньше «Избыток»"
        return None


DEFAULT_THRESHOLDS = Thresholds()


def days_of_supply(available: int, avg_daily_sales: Decimal | None) -> Decimal | None:
    if avg_daily_sales is None or avg_daily_sales <= 0:
        return None
    return (Decimal(max(available, 0)) / avg_daily_sales).quantize(_TENTH, ROUND_HALF_UP)


def stock_status(
    available: int, avg_daily_sales: Decimal | None, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> str | None:
    if avg_daily_sales is None:
        return None
    if avg_daily_sales <= 0:
        return EXCESS if available > 0 else None
    days = Decimal(max(available, 0)) / avg_daily_sales
    if days < thresholds.critical:
        return CRITICAL
    if days < thresholds.low:
        return LOW
    if days > thresholds.excess:
        return EXCESS
    return NORMAL


def days_without_sales(last_sale: date | None, today: date) -> int | None:
    """Сколько дней товар не продавался: от последнего дня с продажей до сегодня. Продаж в данных нет — None
    (сколько именно — неизвестно: «нет продаж с первого дня данных»)."""
    return None if last_sale is None else max((today - last_sale).days, 0)


def end_date(available: int, avg_daily_sales: Decimal | None, today: date) -> date | None:
    """Когда закончится при нынешних продажах: сегодня + целых дней запаса. Остатка нет — сегодня; продажи неизвестны
    или их нет — None (не продаётся — даты окончания нет)."""
    if avg_daily_sales is None or avg_daily_sales <= 0:
        return None
    if available <= 0:
        return today
    return today + timedelta(days=math.floor(Decimal(available) / avg_daily_sales))


def stock_value(qty: int, unit_cost: Decimal | None) -> Decimal | None:
    """Стоимость запаса по себестоимости с НДС; нет себестоимости — None, а не 0 (правило 5)."""
    if unit_cost is None:
        return None
    return (Decimal(max(qty, 0)) * unit_cost).quantize(Decimal("0.01"), ROUND_HALF_UP)
