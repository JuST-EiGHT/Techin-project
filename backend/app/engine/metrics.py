"""Показатели аналитики (шаг 10.3, BUSINESS_LOGIC §10 «Показатели»): ряды и итоги по дням, неделям (пн–вс) и месяцам,
сравнение с прошлым периодом. Чистый модуль: данные по дням готовит services/analytics.

Нет данных ≠ 0 (правило 12): у дня без начислений выручка, прибыль, продажи и возвраты — None, у дня без загруженных
отправлений — заказы None. Точка ряда — сумма дней с данными; ни одного такого дня — None; сколько дней без данных —
в точке. Неделя или месяц, обрезанные краем периода, помечаются partial.
"""
import calendar
from dataclasses import dataclass, fields
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

GRANULARITIES = ("day", "week", "month")
CENT = Decimal("0.01")
TENTH = Decimal("0.1")


@dataclass
class DayData:
    revenue: Decimal | None = None  # «Выручка» начислений; None — начислений за день нет
    profit: Decimal | None = None  # прибыль до налога (магазин — P&L по дням, товары — прибыль по товарам)
    margin_base: Decimal | None = None  # выручка, к которой относится прибыль (у товаров — только с себестоимостью)
    sold_qty: int | None = None  # продано, шт. (sales_daily)
    returned_qty: int | None = None
    returns_amount: Decimal | None = None  # «Возврат выручки», ₽, ≥ 0
    orders: int | None = None  # заказы — отправления по дате создания без отмен; None — отправления не загружены
    ordered_qty: int | None = None  # штуки в заказах


SUMS = tuple(f.name for f in fields(DayData))
ACCRUAL_FIELDS = ("revenue", "profit", "margin_base", "sold_qty", "returned_qty", "returns_amount")
ORDER_FIELDS = ("orders", "ordered_qty")


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def buckets(date_from: date, date_to: date, granularity: str) -> list[tuple[date, date, bool]]:
    """(начало, конец, неполная) — части периода. Неделя — пн–вс, месяц — календарный; край периода обрезает их."""
    if granularity not in GRANULARITIES:
        raise ValueError(granularity)
    out = []
    if granularity == "day":
        d = date_from
        while d <= date_to:
            out.append((d, d, False))
            d += timedelta(days=1)
        return out
    start = date_from - timedelta(days=date_from.weekday()) if granularity == "week" else date_from.replace(day=1)
    while start <= date_to:
        end = start + timedelta(days=6) if granularity == "week" else month_end(start)
        a, b = max(start, date_from), min(end, date_to)
        out.append((a, b, a != start or b != end))
        start = end + timedelta(days=1)
    return out


def _q(v: Decimal | None, step: Decimal = CENT) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(step, ROUND_HALF_UP)


def summarize(days: list[DayData]) -> dict:
    """Итог дней: суммы по дням с данными (ни одного — None), маржа = прибыль / база, средняя цена = выручка / штуки."""
    out: dict = {}
    for name in SUMS:
        known = [getattr(d, name) for d in days if getattr(d, name) is not None]
        out[name] = sum(known, type(known[0])(0)) if known else None
    profit, base = out["profit"], out["margin_base"]
    out["margin"] = _q(profit / base * 100) if profit is not None and base else None
    out["avg_price"] = _q(out["revenue"] / out["sold_qty"]) if out["revenue"] is not None and out["sold_qty"] else None
    out["missing_days"] = sum(d.revenue is None for d in days)
    out["orders_missing_days"] = sum(d.orders is None for d in days)
    return out


def series(days: dict[date, DayData], date_from: date, date_to: date, granularity: str) -> list[dict]:
    """Точки ряда: период точки, неполная ли, сколько в ней дней и дней без данных, показатели."""
    points = []
    for a, b, partial in buckets(date_from, date_to, granularity):
        chunk = [days.get(a + timedelta(days=i), DayData()) for i in range((b - a).days + 1)]
        points.append({"date_from": a, "date_to": b, "partial": partial, "days": len(chunk), **summarize(chunk)})
    return points


def change_pct(current, previous) -> Decimal | None:
    """Изменение к прошлому периоду, %. Нет одной из цифр или прошлая 0 — сравнивать не с чем."""
    if current is None or previous is None or previous == 0:
        return None
    return _q((Decimal(current) - Decimal(previous)) / abs(Decimal(previous)) * 100, TENTH)


VALUES = ("revenue", "profit", "margin", "orders", "ordered_qty", "sold_qty", "returned_qty", "returns_amount", "avg_price")


def changes(current: dict, previous: dict) -> dict:
    """Изменение каждого показателя: %, у маржи — в процентных пунктах (change_pp)."""
    out = {name: change_pct(current.get(name), previous.get(name)) for name in VALUES if name != "margin"}
    cm, pm = current.get("margin"), previous.get("margin")
    out["margin_pp"] = None if cm is None or pm is None else _q(Decimal(cm) - Decimal(pm), TENTH)
    return out


def with_previous(current: list[dict], previous: list[dict]) -> list[dict]:
    """Точки прошлого периода — рядом, по порядку (первая неделя с первой неделей), как на главной (шаг 3.7)."""
    out = []
    for i, point in enumerate(current):
        prev = previous[i] if i < len(previous) else None
        out.append({
            **point,
            "prev": None if prev is None else {k: prev[k] for k in ("date_from", "date_to", "partial", *VALUES)},
        })
    return out
