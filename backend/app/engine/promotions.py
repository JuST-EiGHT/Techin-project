"""Акции: маржа в акции и признак «участвует весь остаток». Чистые функции — без БД и HTTP.

Маржа здесь — ПРОСТАЯ, % от акционной цены:
    (P × (1 − комиссия − эквайринг) − C × НДС) / P × 100
где C × НДС — себестоимость с НДС поставщика: ставка из записи себестоимости или налогового профиля
(services/costs.cost_prices_with_vat), а не одна на всех.
без логистики, хранения, рекламы и возвратов. Полноценный расчёт — после калькулятора
минимальной цены (шаг 4.2, docs/BUSINESS_LOGIC.md §4).
Нет себестоимости или комиссии → None, а не 0 (правило 5: неизвестная себестоимость ≠ 0).
"""
from decimal import ROUND_HALF_UP, Decimal

LOW_MARGIN_PCT = Decimal("10")  # порог «низкой маржи» по умолчанию (BUSINESS_LOGIC §7)

NEGATIVE, LOW, OK = "negative", "low", "ok"

# С 6.10.2026 в этих акциях нельзя ограничить количество: участвует весь остаток (BUSINESS_LOGIC §9)
_WHOLE_STOCK_TITLES = ("эластичный бустинг", "скидка на сток")
_WHOLE_STOCK_TYPES = ("ELASTIC_BOOSTING", "STOCK_DISCOUNT")


def margin_in_promo(
    action_price: Decimal | None,
    cost_with_vat: Decimal | None,
    commission_pct: Decimal | None,
    acquiring_pct: Decimal | None = None,
) -> Decimal | None:
    if action_price is None or action_price <= 0 or cost_with_vat is None or commission_pct is None:
        return None
    share = 1 - commission_pct / 100 - (acquiring_pct or Decimal(0)) / 100
    net = action_price * share - cost_with_vat
    return (net / action_price * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)


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
