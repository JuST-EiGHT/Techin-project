"""Полнота данных Ozon по неделям и месяцам (шаг 10.12). Чистый модуль.

День закрыт, если за него есть начисления: из отчёта по начислениям (файл — точнее, его и берём) или из Ozon API по
дням. Неделя (пн–вс, обрезанная краем месяца) — чем закрыта: файл / API / вместе / частично / ничем; дни после
сегодняшнего не ждём. Месяц — ещё и отчёт о реализации: есть (файл, API), ещё не время (месяц не закончился или до 5-го
числа следующего — Ozon формирует его до 5-го) или нет — тогда «Получить через API» или «Загрузить файл».
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

XLSX, API = "xlsx", "api"
FULL_XLSX, FULL_API, MIXED, PARTIAL, NONE, FUTURE = "xlsx", "api", "mixed", "partial", "none", "future"
HAVE, CURRENT, NOT_YET, MISSING = "have", "current", "not_yet", "missing"
MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь",
          "Декабрь")
REALIZATION_DAY = 5  # Ozon формирует отчёт о реализации за месяц до 5-го числа следующего


@dataclass
class Week:
    start: date
    end: date
    days: int = 0  # дней, за которые данные уже должны быть
    xlsx: int = 0
    api: int = 0
    missing: list[date] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.days == 0:
            return FUTURE
        if self.missing:
            return PARTIAL if self.xlsx or self.api else NONE
        return MIXED if self.xlsx and self.api else FULL_XLSX if self.xlsx else FULL_API


@dataclass
class Month:
    year: int
    month: int
    start: date
    end: date
    weeks: list[Week]
    realization: str  # have | current | not_yet | missing
    realization_sources: list[str]  # file / api — чем есть
    realization_ids: list[str]

    @property
    def title(self) -> str:
        return f"{MONTHS[self.month - 1]} {self.year}"

    @property
    def days(self) -> int:
        return sum(w.days for w in self.weeks)

    @property
    def missing(self) -> int:
        return sum(len(w.missing) for w in self.weeks)


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def add_months(d: date, n: int) -> date:
    m = d.year * 12 + d.month - 1 + n
    return date(m // 12, m % 12 + 1, 1)


def realization_state(year: int, month: int, today: date, have: bool) -> str:
    if have:
        return HAVE
    first = date(year, month, 1)
    if first >= month_start(today):
        return CURRENT
    if add_months(first, 1) == month_start(today) and today.day <= REALIZATION_DAY:
        return NOT_YET
    return MISSING


def months(days: dict[date, str | None], realization: dict[tuple[int, int], list[tuple[str, str]]], today: date,
           count: int) -> list[Month]:
    """Последние count месяцев (с текущим), новые сверху. days — день → xlsx | api | None (из P&L-покрытия, 3.2);
    realization — (год, месяц) → [(источник, id отчёта)]."""
    out = []
    for i in range(count):
        first = add_months(month_start(today), -i)
        last = month_end(first)
        weeks: list[Week] = []
        d = first
        while d <= last:
            end = min(d + timedelta(days=6 - d.weekday()), last)
            w = Week(d, end)
            day = d
            while day <= end:
                if day < today:  # сегодняшние начисления ещё не закончились
                    w.days += 1
                    src = days.get(day)
                    if src == XLSX:
                        w.xlsx += 1
                    elif src == API:
                        w.api += 1
                    else:
                        w.missing.append(day)
                day += timedelta(days=1)
            weeks.append(w)
            d = end + timedelta(days=1)
        reports = realization.get((first.year, first.month), [])
        out.append(Month(first.year, first.month, first, last, weeks,
                         realization_state(first.year, first.month, today, bool(reports)),
                         sorted({s for s, _ in reports}), [rid for _, rid in reports]))
    return out
