"""Сверка расчётного остатка счёта с выпиской банка (шаг 10.20). Чистый модуль — без БД.

Остаток сервиса на конец дня — начальный остаток счёта (на начало дня `as_of`) + операции с `as_of` по этот день;
раньше `as_of − 1` он неизвестен. Остаток банка на конец дня — точки банка: остаток на конец дня (`closing`) или на
начало (`opening` = конец предыдущего дня). Между соседними точками остаток банка по дням — от левой точки плюс операции
банка; если так не получается правая точка (выписка за часть дней не загружена), внутри отрезка дни не сравниваем —
только сами точки.

Расхождение = сервис − банк. Последний сравнимый день сходится — «сходится». Нет — идём назад, пока расходится: первый
день этой полосы — «с какого дня началось»; полоса дошла до первого сравнимого дня — расходится с начала данных банка
(обычно неверный начальный остаток). Нет точек банка — «нет данных банка», нет начального остатка — «остаток не указан».
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Point:
    """Остаток по данным банка на конец дня `day`."""

    day: date
    amount: Decimal


@dataclass
class Day:
    day: date
    bank: Decimal
    service: Decimal
    diff: Decimal  # сервис − банк
    point: bool  # остаток банка из выписки, а не досчитан по операциям банка


@dataclass
class Result:
    status: str  # ok | diff | no_bank | no_opening | no_overlap
    last: Day | None = None  # последний сравнимый день
    since: date | None = None  # с какого дня расходится
    from_start: bool = False  # расходится с первого сравнимого дня
    matched_until: date | None = None  # последний сходившийся день перед расхождением
    jump: Decimal | None = None  # на сколько разошлось в день `since`
    days: list[Day] = field(default_factory=list)  # сравнимые дни по порядку


def points_from(balances: list[tuple[date, str, Decimal]]) -> list[Point]:
    """(день, opening | closing, сумма) → точки на конец дня; один день — последняя переданная запись."""
    out: dict[date, Decimal] = {}
    for day, kind, amount in balances:
        end = day - timedelta(days=1) if kind == "opening" else day
        out[end] = Decimal(amount)
    return [Point(d, out[d]) for d in sorted(out)]


def bank_days(points: list[Point], moves: dict[date, Decimal]) -> dict[date, tuple[Decimal, bool]]:
    """Остаток банка на конец дня: точки и дни между ними, где операции банка сходятся с правой точкой."""
    out: dict[date, tuple[Decimal, bool]] = {p.day: (p.amount, True) for p in points}
    for left, right in zip(points, points[1:]):
        running, days = left.amount, []
        d = left.day + timedelta(days=1)
        while d < right.day:
            running += moves.get(d, ZERO)
            days.append((d, running))
            d += timedelta(days=1)
        if running + moves.get(right.day, ZERO) == right.amount:  # операции банка за отрезок полные
            out.update({day: (value, False) for day, value in days})
    return out


def reconcile(points: list[Point], bank_moves: dict[date, Decimal], opening: tuple[date, Decimal] | None,
              service_moves: dict[date, Decimal]) -> Result:
    if not points:
        return Result("no_bank")
    if opening is None:
        return Result("no_opening")
    as_of, start = opening
    bank = bank_days(points, bank_moves)
    first = as_of - timedelta(days=1)
    days: list[Day] = []
    running, d = Decimal(start), first
    for day in sorted(bank):
        if day < first:
            continue
        while d < day:
            d += timedelta(days=1)
            running += service_moves.get(d, ZERO)
        value, point = bank[day]
        days.append(Day(day, value.quantize(CENT), running.quantize(CENT), (running - value).quantize(CENT), point))
    if not days:
        return Result("no_overlap")
    last = days[-1]
    if last.diff == 0:
        return Result("ok", last=last, days=days)
    i = len(days) - 1
    while i > 0 and days[i - 1].diff != 0:
        i -= 1
    since = days[i]
    before = days[i - 1] if i > 0 else None
    return Result("diff", last=last, since=since.day, from_start=before is None,
                  matched_until=before.day if before else None,
                  jump=since.diff - (before.diff if before else ZERO), days=days)
