"""Акции: уровень маржи и признак «участвует весь остаток». Чистые функции — без БД и HTTP.

Маржа в акции с шага 4.2 — калькулятор цены при акционной цене (engine/price_calculator.margin_at, services/pricing):
с логистикой, рекламой, НДС и налогом с выручки. Нет себестоимости, комиссии или логистики → None, а не 0 (правило 5).
"""
from decimal import Decimal

LOW_MARGIN_PCT = Decimal("10")  # порог «низкой маржи» по умолчанию (BUSINESS_LOGIC §7)

NEGATIVE, LOW, OK = "negative", "low", "ok"

# С 6.10.2026 в этих акциях нельзя ограничить количество: участвует весь остаток (BUSINESS_LOGIC §9)
_WHOLE_STOCK_TITLES = ("эластичный бустинг", "скидка на сток")
_WHOLE_STOCK_TYPES = ("ELASTIC_BOOSTING", "STOCK_DISCOUNT")


def margin_level(margin: Decimal | None, low: Decimal = LOW_MARGIN_PCT) -> str | None:
    if margin is None:
        return None
    if margin < 0:
        return NEGATIVE
    return LOW if margin < low else OK


def is_whole_stock(title: str | None, action_type: str | None) -> bool:
    t = (title or "").lower().replace("ё", "е")
    kind = (action_type or "").upper()
    return any(m in t for m in _WHOLE_STOCK_TITLES) or any(m in kind for m in _WHOLE_STOCK_TYPES)


def is_boosting(title: str | None, action_type: str | None) -> bool:
    """«Эластичный» и «Максимальный бустинг»: выйти из них методом выхода нельзя — Ozon убирает товар через цену в
    акции (документация /v2/actions/products/deactivate)."""
    t = (title or "").lower().replace("ё", "е")
    return "бустинг" in t or "BOOST" in (action_type or "").upper()
