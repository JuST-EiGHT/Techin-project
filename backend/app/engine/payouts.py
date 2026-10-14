"""Выплаты Ozon (шаг 3.6): график по фактическим выплатам и прогноз. Чистый модуль — без БД и HTTP.

- detect_schedule: по выплатам за последние 8–12 недель — ежедневный (почти каждый рабочий день),
  еженедельный (какой день недели) или нерегулярный; лаг — за какую неделю продаж выплата (сумма
  выплаты ≈ начисления недели N недель назад); комиссия гибкого графика — по разнице; уверенность.
- forecast: выплаты на несколько недель вперёд. Дата — по графику, выходной или праздник → следующий
  рабочий день. Сумма — начисления недели продаж минус комиссия («по графику»); неделя ещё не закончилась
  или её нет — среднее за 4 последние полные недели («оценка»). Выплаты по уже закончившимся неделям не
  обещают больше, чем лежит на балансе у Ozon.
Выплата в выходной (перенос с праздника) относится к дню графика — «задуманному» дню недели.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from app.engine.workdays import is_workday, next_workday

ZERO = Decimal(0)
CENT = Decimal("0.01")
ON_DAYS = ("по понедельникам", "по вторникам", "по средам", "по четвергам", "по пятницам", "по субботам",
           "по воскресеньям")
MIN_PAYOUTS = 3  # меньше выплат в истории — график не определяем
MAX_LAG = 6


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def intended_day(day: date) -> date:
    """День графика, с которого выплату перенесли: назад через нерабочие будни (праздники), не через выходные."""
    while (prev := day - timedelta(days=1)).weekday() < 5 and not is_workday(prev):
        day = prev
    return day


@dataclass
class Payout:
    day: date
    amount: Decimal  # > 0


@dataclass
class Schedule:
    mode: str  # daily | weekly | irregular
    weekday: int | None = None
    lag_weeks: int | None = None
    fee_pct: Decimal = ZERO
    source: str = "auto"  # auto — по выплатам, standard — стандартный график Ozon, user — свой
    confidence: str | None = None  # high | medium | low — у определённого по выплатам
    payouts_used: int = 0

    @property
    def title(self) -> str:
        if self.mode == "daily":
            text = "каждый рабочий день"
        elif self.mode == "weekly":
            text = f"раз в неделю {ON_DAYS[self.weekday]}" if self.weekday is not None else "раз в неделю"
            if self.lag_weeks is not None:
                text += f", за неделю продаж {self.lag_weeks} нед. назад"
        else:
            text = "нерегулярно"
        if self.fee_pct:
            text += f", комиссия {format(self.fee_pct.normalize(), 'f').replace('.', ',')} %"
        return text


@dataclass
class Forecast:
    day: date
    amount: Decimal | None  # None — оценить нечем (нет ни одной полной недели начислений)
    kind: str  # schedule — по начислениям закончившейся недели, estimate — оценка по среднему
    week_from: date | None = None  # неделя продаж, за которую выплата
    week_to: date | None = None


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, ROUND_HALF_UP)


def detect_schedule(
    payouts: list[Payout], weekly_accrued: dict[date, Decimal], today: date, weeks: int = 12
) -> Schedule | None:
    """None — выплат слишком мало, чтобы судить (новый ключ): берётся стандартный график."""
    recent = sorted((p for p in payouts if p.amount > 0 and today - timedelta(weeks=weeks) <= p.day <= today),
                    key=lambda p: p.day)
    if len(recent) < MIN_PAYOUTS:
        return None
    first, last = recent[0].day, recent[-1].day
    workdays = sum(is_workday(first + timedelta(days=i)) for i in range((last - first).days + 1))
    if len(recent) >= 10 and len(recent) >= 0.7 * workdays:
        return Schedule("daily", confidence="high", payouts_used=len(recent))

    counts: dict[int, int] = {}
    for p in recent:
        wd = intended_day(p.day).weekday()
        counts[wd] = counts.get(wd, 0) + 1
    weekday, hits = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
    if hits < 0.75 * len(recent):
        return Schedule("irregular", confidence="low", payouts_used=len(recent))

    regular = [p for p in recent if intended_day(p.day).weekday() == weekday]
    best: tuple[Decimal, int, list[Decimal]] | None = None  # (медианная ошибка, лаг, доли «выплата / начислено»)
    for lag in range(1, MAX_LAG + 1):
        ratios = []
        for p in regular:
            acc = weekly_accrued.get(monday(intended_day(p.day)) - timedelta(weeks=lag))
            if acc is not None and acc > 0:
                ratios.append(p.amount / acc)
        if len(ratios) < max(MIN_PAYOUTS, len(regular) // 2):
            continue
        error = Decimal(median(abs(1 - r) for r in ratios))
        if best is None or error < best[0]:
            best = (error, lag, ratios)
    if best is None:
        return Schedule("weekly", weekday, confidence="low", payouts_used=len(recent))

    _, lag, ratios = best
    fee = Decimal(median(1 - r for r in ratios)) * 100
    fee = ZERO if fee < Decimal("0.3") or fee > 10 else fee.quantize(Decimal("0.1"), ROUND_HALF_UP)
    after_fee = Decimal(median(abs(1 - r / (1 - fee / 100)) for r in ratios))
    confidence = "high" if after_fee <= Decimal("0.03") else "medium" if after_fee <= Decimal("0.10") else "low"
    return Schedule("weekly", weekday, lag, fee, confidence=confidence, payouts_used=len(recent))


def _average(weekly_accrued: dict[date, Decimal], before: date, n: int = 4) -> Decimal | None:
    weeks = sorted((w for w in weekly_accrued if w + timedelta(days=6) < before), reverse=True)[:n]
    return sum((weekly_accrued[w] for w in weeks), ZERO) / len(weeks) if weeks else None


def expected(schedule: Schedule, payout_day: date, weekly_accrued: dict[date, Decimal]) -> tuple[date, Decimal | None] | None:
    """За какую неделю продаж выплата этого дня и сколько ожидали по графику. None — неделю не определить."""
    if schedule.mode != "weekly" or schedule.lag_weeks is None:
        return None
    week = monday(intended_day(payout_day)) - timedelta(weeks=schedule.lag_weeks)
    acc = weekly_accrued.get(week)
    return week, None if acc is None else _q(max(acc, ZERO) * (1 - schedule.fee_pct / 100))


def forecast(
    schedule: Schedule,
    today: date,
    weekly_accrued: dict[date, Decimal],
    paid_today: bool,
    balance: Decimal | None,
    horizon_weeks: int = 8,
) -> list[Forecast]:
    start = today + timedelta(days=1) if paid_today else today
    end = today + timedelta(weeks=horizon_weeks)
    keep = 1 - schedule.fee_pct / 100
    avg = _average(weekly_accrued, today)
    out: list[Forecast] = []
    if schedule.mode == "daily":
        daily = None if avg is None else _q(max(avg, ZERO) * keep / 5)
        day = start
        while day <= end:
            if is_workday(day):
                out.append(Forecast(day, daily, "estimate"))
            day += timedelta(days=1)
        return out
    if schedule.mode != "weekly" or schedule.weekday is None:
        return out  # нерегулярный график — даты не прогнозируем

    remaining = balance if balance is not None and balance > 0 else None
    base = monday(start) + timedelta(days=schedule.weekday)
    while base <= end:
        day = next_workday(base)
        if day >= start:
            item = Forecast(day, None, "estimate")
            if schedule.lag_weeks is not None:
                week = monday(base) - timedelta(weeks=schedule.lag_weeks)
                item.week_from, item.week_to = week, week + timedelta(days=6)
                acc = weekly_accrued.get(week)
                if acc is not None and item.week_to < today:
                    amount = _q(max(acc, ZERO) * keep)
                    if remaining is not None:
                        amount = min(amount, remaining)
                        remaining -= amount
                    item.amount, item.kind = amount, "schedule"
            if item.kind == "estimate" and avg is not None:
                item.amount = _q(max(avg, ZERO) * keep)
            out.append(item)
        base += timedelta(weeks=1)
    return out


def overdue(
    schedule: Schedule, today: date, payout_days: set[date], loaded_days: set[date], days_back: int = 14
) -> list[date]:
    """Дни графика за последние две недели, когда выплаты не было (и за 3 дня после — тоже). Только по дням,
    за которые баланс загружен: нет данных — не «просрочка»."""
    if schedule.mode != "weekly" or schedule.weekday is None:
        return []
    out = []
    base = monday(today - timedelta(days=days_back)) + timedelta(days=schedule.weekday)
    while base < today:
        day = next_workday(base)
        window = [day + timedelta(days=i) for i in range(4)]
        if day >= today - timedelta(days=days_back) and window[-1] < today:
            if all(d in loaded_days for d in window) and not any(d in payout_days for d in window):
                out.append(day)
        base += timedelta(weeks=1)
    return out
