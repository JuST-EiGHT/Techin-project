"""Результаты акций (шаг 10.16): продажи, выручка и прибыль товара за время участия против такого же периода до входа.

- Окно «во время» — с дня входа (по Москве) по день выхода, конец акции или сегодня, но не длиннее MAX_DAYS (длинное
  участие сравниваем по первым дням — дальше на продажи влияет уже не вход в акцию). «До» — столько же дней перед входом.
- Сравниваем средние за день с данными: день без начислений — пропуск, а не 0 (правило 12); в окне нет ни одного дня с
  данными — сравнения нет. «До», приведённое к дням с данными «во время», — база изменения.
- Прибыль — до налога, как «Прибыль по товарам»; нет себестоимости в какой-то из дней с продажами — прибыли нет (правило 5).
- По акции в целом — сумма товаров, у которых оба окна с данными.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

MAX_DAYS = 90
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Day:
    """Товар за день с данными: продано, выручка, прибыль (None — нет себестоимости)."""

    qty: int = 0
    revenue: Decimal = Decimal(0)
    profit: Decimal | None = Decimal(0)


@dataclass
class Totals:
    date_from: date
    date_to: date
    days: int
    days_with_data: int
    qty: int
    revenue: Decimal
    profit: Decimal | None

    def scaled(self, days: int) -> "Totals":
        """Те же средние за день — на другое число дней с данными (база сравнения «до»)."""
        k = Decimal(days) / Decimal(self.days_with_data)
        return Totals(self.date_from, self.date_to, self.days, days, round(self.qty * k),
                      (self.revenue * k).quantize(CENT, ROUND_HALF_UP),
                      None if self.profit is None else (self.profit * k).quantize(CENT, ROUND_HALF_UP))


def windows(entered: date, ended: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """(во время, до): одинаковой длины, «до» заканчивается накануне входа."""
    ended = max(ended, entered)
    end = min(ended, entered + timedelta(days=MAX_DAYS - 1))
    n = (end - entered).days + 1
    return (entered, end), (entered - timedelta(days=n), entered - timedelta(days=1))


def totals(days: dict[date, Day | None], date_from: date, date_to: date) -> Totals:
    """days: день → значения товара (день с данными) или None / нет ключа — данных нет."""
    qty, revenue, profit, known = 0, Decimal(0), Decimal(0), 0
    profit_known = True
    d = date_from
    while d <= date_to:
        v = days.get(d)
        if v is not None:
            known += 1
            qty += v.qty
            revenue += v.revenue
            if v.profit is None:
                profit_known = False
            else:
                profit += v.profit
        d += timedelta(days=1)
    return Totals(date_from, date_to, (date_to - date_from).days + 1, known, qty, revenue.quantize(CENT, ROUND_HALF_UP),
                  profit.quantize(CENT, ROUND_HALF_UP) if profit_known and known else None)


def _pct(now: Decimal, base: Decimal) -> Decimal | None:
    return ((now - base) / base * 100).quantize(Decimal("0.1"), ROUND_HALF_UP) if base > 0 else None


def compare(during: Totals, before: Totals) -> dict:
    """Изменение к «до», приведённому к дням с данными «во время»; нет данных в одном из окон — None."""
    if not during.days_with_data or not before.days_with_data:
        return {"known": False, "base": None, "qty_change_pct": None, "revenue_change_pct": None, "profit_change": None}
    base = before.scaled(during.days_with_data)
    return {
        "known": True, "base": base,
        "qty_change_pct": _pct(Decimal(during.qty), Decimal(base.qty)),
        "revenue_change_pct": _pct(during.revenue, base.revenue),
        "profit_change": None if during.profit is None or base.profit is None else during.profit - base.profit,
    }


def combine(pairs: list[tuple[Totals, Totals]]) -> dict:
    """По акции в целом: сумма «во время» и баз «до» товаров с данными в обоих окнах."""
    known = [(d, compare(d, b)["base"]) for d, b in pairs if d.days_with_data and b.days_with_data]
    if not known:
        return {"products": 0, "qty": None, "qty_before": None, "revenue": None, "revenue_before": None, "profit": None,
                "profit_before": None, "qty_change_pct": None, "revenue_change_pct": None, "profit_change": None}
    qty = sum(d.qty for d, _ in known)
    qty_before = sum(b.qty for _, b in known)
    revenue = sum((d.revenue for d, _ in known), Decimal(0))
    revenue_before = sum((b.revenue for _, b in known), Decimal(0))
    with_profit = [(d, b) for d, b in known if d.profit is not None and b.profit is not None]
    profit = sum((d.profit for d, _ in with_profit), Decimal(0)) if with_profit else None
    profit_before = sum((b.profit for _, b in with_profit), Decimal(0)) if with_profit else None
    return {
        "products": len(known), "qty": qty, "qty_before": qty_before, "revenue": revenue, "revenue_before": revenue_before,
        "profit": profit, "profit_before": profit_before,
        "qty_change_pct": _pct(Decimal(qty), Decimal(qty_before)),
        "revenue_change_pct": _pct(revenue, revenue_before),
        "profit_change": None if profit is None else profit - profit_before,
    }
