"""Продажи по дням (шаг 3.5) — чистый расчёт без БД.

aggregate: строки начислений → продано и возвращено по артикулу за день (строки «Выручка» и «Возврат
выручки»; без артикула — по номеру отправления, если товар в нём один, как в прибыли по товарам).
avg_daily: продажи в день за окно — только по дням, за которые данные есть (нет данных ≠ 0 продаж);
мало дней с данными — None («нет данных»), а не заниженное среднее.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.engine.product_pnl import LineIn
from app.integrations.xlsx.common import norm

ZERO = Decimal(0)
REVENUE, RETURN = "выручка", "возврат выручки"
MIN_DAYS = 7  # меньше дней с данными в окне — среднее не считаем


@dataclass
class DaySales:
    day: date
    offer_id: str
    sku: str = ""
    qty_sold: int = 0
    qty_returned: int = 0
    revenue: Decimal = ZERO
    returns_amount: Decimal = ZERO  # сумма возвратов, положительная


def aggregate(lines: list[LineIn], posting_offers: dict[str, set[str]]) -> tuple[list[DaySales], int]:
    """→ (продажи по дню и артикулу, строк продаж и возвратов без товара — их не к чему отнести)."""
    out: dict[tuple[date, str], DaySales] = {}
    unattributed = 0
    for ln in lines:
        kind = norm(ln.type_name)
        if kind not in (REVENUE, RETURN):
            continue
        offer = ln.offer_id
        if not offer and ln.posting_number and len(offers := posting_offers.get(ln.posting_number, set())) == 1:
            offer = next(iter(offers))
        if not offer:
            unattributed += 1
            continue
        row = out.setdefault((ln.day, offer), DaySales(ln.day, offer))
        row.sku = row.sku or ln.sku
        qty = abs(ln.quantity) if ln.quantity else 1  # как в отчёте: пусто или 0 — одна штука
        if kind == REVENUE:
            row.qty_sold += qty
            row.revenue += ln.amount
        else:
            row.qty_returned += qty
            row.returns_amount += -ln.amount
    return sorted(out.values(), key=lambda r: (r.day, r.offer_id)), unattributed


def avg_daily(net_units: int, days_with_data: int, min_days: int = MIN_DAYS) -> Decimal | None:
    """Продано за вычетом возвратов / дней с данными. Меньше min_days дней с данными — None."""
    if days_with_data < min_days:
        return None
    return (Decimal(max(net_units, 0)) / days_with_data).quantize(Decimal("0.001"), ROUND_HALF_UP)
