"""Запас товара на складе: дни запаса и статус. Чистые функции — без БД и HTTP.

days_of_supply = остаток / среднесуточные продажи
статус:  < 7 дней — critical, < 14 — low, > 60 — excess, иначе normal

Нет данных о продажах (avg_daily_sales = None) → дни запаса и статус None («нет данных»),
а не 0: ноль выглядел бы как «товар кончился».
Продажи известны и равны нулю → дни запаса не определены (None), но лежащий без
продаж остаток — это excess.
"""
from decimal import ROUND_HALF_UP, Decimal

CRITICAL_DAYS = 7
LOW_DAYS = 14
EXCESS_DAYS = 60

CRITICAL, LOW, NORMAL, EXCESS = "critical", "low", "normal", "excess"
STATUSES = (CRITICAL, LOW, NORMAL, EXCESS)

_TENTH = Decimal("0.1")


def days_of_supply(available: int, avg_daily_sales: Decimal | None) -> Decimal | None:
    if avg_daily_sales is None or avg_daily_sales <= 0:
        return None
    return (Decimal(max(available, 0)) / avg_daily_sales).quantize(_TENTH, ROUND_HALF_UP)


def stock_status(available: int, avg_daily_sales: Decimal | None) -> str | None:
    if avg_daily_sales is None:
        return None
    if avg_daily_sales <= 0:
        return EXCESS if available > 0 else None
    days = Decimal(max(available, 0)) / avg_daily_sales
    if days < CRITICAL_DAYS:
        return CRITICAL
    if days < LOW_DAYS:
        return LOW
    if days > EXCESS_DAYS:
        return EXCESS
    return NORMAL
