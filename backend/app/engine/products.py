"""Страница товаров (шаг 4.1): индекс цены, цена по дням, фильтры и сортировка. Чистые функции — без БД и HTTP.

Маржа при текущей цене — калькулятор цены (шаг 4.2, engine/price_calculator.py).
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

_CENT = Decimal("0.01")

# Цветовой индекс цены Ozon (`price_indexes.color_index`): с 2025 года бывает и с префиксом COLOR_INDEX_.
# WITHOUT_INDEX — индекса нет (товар не с чем сравнить) → None.
INDEX_LEVELS = ("super", "green", "yellow", "red")
_INDEX_ALIASES = {"SUPER": "super", "GREEN": "green", "YELLOW": "yellow", "RED": "red"}

# Быстрые фильтры страницы: несколько сразу — товар должен подходить под все
NEGATIVE, LOW_STOCK, CRITICAL_STOCK, IN_PROMO, NO_COST = "negative", "low_stock", "critical_stock", "in_promo", "no_cost"
FILTERS = (NEGATIVE, LOW_STOCK, CRITICAL_STOCK, IN_PROMO, NO_COST)

SORTS = ("offer_id", "name", "price", "min_price", "commission", "cost", "stock", "days", "margin", "index", "promo",
         "last_sale")


def price_index_level(raw: str | None) -> str | None:
    """«GREEN», «COLOR_INDEX_GREEN» → green; WITHOUT_INDEX, пусто и неизвестное → None."""
    if not raw:
        return None
    key = raw.strip().upper().removeprefix("COLOR_INDEX_")
    return _INDEX_ALIASES.get(key)


def positive(value: Decimal | None) -> Decimal | None:
    """Цены Ozon: 0 = «не задана» — показываем как нет данных, а не как 0 ₽."""
    return value if value is not None and value > 0 else None


def acquiring_pct(acquiring: Decimal | None, price: Decimal | None) -> Decimal | None:
    """Эквайринг в % от цены. Ozon отдаёт его в рублях при текущей цене (`acquiring`), а платится он процентом от цены
    продажи — для расчёта при другой цене нужен процент."""
    price = positive(price)
    if acquiring is None or price is None:
        return None
    return (acquiring / price * 100).quantize(Decimal("0.001"), ROUND_HALF_UP)


def percent_of(price: Decimal | None, pct: Decimal | None) -> Decimal | None:
    """Комиссия или эквайринг в рублях от цены."""
    if price is None or pct is None:
        return None
    return (price * pct / 100).quantize(_CENT, ROUND_HALF_UP)


@dataclass
class PricePoint:
    """Снимок цены (шаг 1.2: новая запись — только при изменении) — день по Москве и поля."""

    day: date
    price: Decimal | None
    min_price: Decimal | None = None
    marketing_price: Decimal | None = None


def daily_prices(snapshots: list[PricePoint], date_from: date, date_to: date) -> list[dict]:
    """Цена на конец каждого дня: последний снимок не позже этого дня. Дни до первого снимка — None
    (истории ещё нет, а не цена 0). Снимки — в любом порядке; в один день — берётся последний по списку."""
    ordered = sorted(enumerate(snapshots), key=lambda p: (p[1].day, p[0]))
    out: list[dict] = []
    i, current = 0, None
    day = date_from
    while day <= date_to:
        while i < len(ordered) and ordered[i][1].day <= day:
            current = ordered[i][1]
            i += 1
        out.append({
            "date": day,
            "price": positive(current.price) if current else None,
            "min_price": positive(current.min_price) if current else None,
            "marketing_price": positive(current.marketing_price) if current else None,
        })
        day += timedelta(days=1)
    return out


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def matches(search: str, *fields: Any) -> bool:
    """Поиск без учёта регистра, «ё» = «е» и лишних пробелов: часть любого из полей."""
    q = normalize(search)
    return not q or any(q in normalize(str(f)) for f in fields if f is not None)


def passes(row: dict, filters: set[str]) -> bool:
    """Строка подходит под все выбранные быстрые фильтры."""
    checks: dict[str, Callable[[dict], bool]] = {
        NEGATIVE: lambda r: r["margin"] is not None and r["margin"] < 0,
        LOW_STOCK: lambda r: r["stock_status"] in ("critical", "low"),
        CRITICAL_STOCK: lambda r: r["stock_status"] == "critical",
        IN_PROMO: lambda r: bool(r["promotions"]),
        NO_COST: lambda r: r["cost_price"] is None,
    }
    return all(checks[f](row) for f in filters)


def _index_rank(level: str | None) -> int | None:
    return INDEX_LEVELS.index(level) if level in INDEX_LEVELS else None  # super лучше всех, red хуже


_SORT_KEYS: dict[str, Callable[[dict], Any]] = {
    "offer_id": lambda r: r["offer_id"].lower(),
    "name": lambda r: normalize(r["name"]) if r["name"] else None,
    "price": lambda r: r["price"],
    "min_price": lambda r: r["min_price"],
    "commission": lambda r: r["commission_pct"],
    "cost": lambda r: r["cost_price"],
    "stock": lambda r: r["stock"],
    "days": lambda r: r["days_of_supply"],
    "margin": lambda r: r["margin"],
    "index": lambda r: _index_rank(r["price_index"]),
    "promo": lambda r: len(r["promotions"]) or None,
    "last_sale": lambda r: r["last_sale"],
}


def sort_rows(rows: list[dict], sort: str, order: str) -> list[dict]:
    """Сортировка по колонке; пустые значения — всегда в конце, при равных — по артикулу."""
    key = _SORT_KEYS[sort]
    by_offer = sorted(rows, key=lambda r: r["offer_id"].lower())
    filled = [r for r in by_offer if key(r) is not None]
    empty = [r for r in by_offer if key(r) is None]
    return sorted(filled, key=key, reverse=order == "desc") + empty
