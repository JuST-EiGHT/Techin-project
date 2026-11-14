"""Кредиторская задолженность перед поставщиком (шаг 10.29): чистый расчёт без БД и HTTP.

Долг = начисленное поставщику − оплаченное. Начисления: долг на начало учёта, продажи (если поставщик получает деньги
после продажи — себестоимость проданного с НДС) и закупки с отсрочкой. Оплаты закрывают самые старые долги первыми
(FIFO по сроку оплаты): так видно, что просрочено, а что ещё не наступило. Оплат больше, чем начислено, — это не
«отрицательный долг», а аванс поставщику: он показывается отдельно.

Деньги — Decimal, округление до копейки; даты — календарные (МСК считает вызывающий).
"""
import calendar as month_calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
# возрастные группы просрочки, дней: ключ — граница «до», None — без верхней границы
OVERDUE_BUCKETS = (("d1_30", 30), ("d31_60", 60), ("d61_plus", None))
DUE_WINDOWS = (7, 14, 30)


def q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Accrual:
    """Начисление долга: `day` — когда возник, `due` — срок оплаты, `amount` > 0."""

    day: date
    due: date | None  # None — срок неизвестен (долг на начало учёта без срока): не просрочен и не в «ближайших»
    amount: Decimal
    kind: str  # opening | sale | purchase
    label: str
    ref: str | None = None  # у закупки — entry_id: страница ведёт к записи
    period: tuple[date, date] | None = None  # у продаж — за какие дни


@dataclass(frozen=True)
class Payment:
    day: date
    amount: Decimal  # > 0; меньше нуля — возврат денег от поставщика
    source: str  # manual | operation
    ref: str | None = None


@dataclass
class Open:
    """Неоплаченная часть начисления."""

    accrual: Accrual
    remaining: Decimal
    overdue_days: int  # 0 — срок не наступил, сегодня или не задан

    @property
    def overdue(self) -> bool:
        return self.overdue_days > 0


@dataclass
class Ledger:
    accrued: Decimal = ZERO
    paid: Decimal = ZERO
    debt: Decimal = ZERO  # неоплаченное начисленное
    prepaid: Decimal = ZERO  # оплачено сверх начисленного — аванс поставщику
    overdue: Decimal = ZERO
    not_due: Decimal = ZERO
    items: list[Open] = field(default_factory=list)  # только с остатком, по сроку оплаты
    buckets: dict[str, Decimal] = field(default_factory=dict)  # no_term, not_due, d1_30, d31_60, d61_plus
    due_in: dict[int, Decimal] = field(default_factory=dict)  # к оплате в ближайшие N дней (не просроченное)


def sale_period(day: date, period: str, pay_day: int) -> tuple[date, date, date]:
    """Период оплаты продаж дня: (начало, конец, срок). Неделя — оплата на следующей неделе в день `pay_day` (1 —
    понедельник … 7 — воскресенье), месяц — в следующем месяце числом `pay_day` (31 — последний день): как у платежей
    поставщику в календаре планирования (шаг 3.9)."""
    if period == "month":
        start = day.replace(day=1)
        end = day.replace(day=month_calendar.monthrange(day.year, day.month)[1])
        index = day.year * 12 + day.month  # следующий месяц
        year, month = index // 12, index % 12 + 1
        return start, end, date(year, month, min(pay_day, month_calendar.monthrange(year, month)[1]))
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6), start + timedelta(days=7 + pay_day - 1)


def settle(accruals: list[Accrual], payments: list[Payment], today: date) -> Ledger:
    """Разнести оплаты по начислениям (самые ранние сроки — первыми) и разложить остаток по срокам."""
    ledger = Ledger(buckets={"no_term": ZERO, "not_due": ZERO, **{key: ZERO for key, _ in OVERDUE_BUCKETS}},
                    due_in={n: ZERO for n in DUE_WINDOWS})
    ledger.accrued = q(sum((a.amount for a in accruals), ZERO))
    ledger.paid = q(sum((p.amount for p in payments), ZERO))
    left = max(ledger.paid, ZERO)  # возврат от поставщика (минус) может увести сумму ниже нуля — тогда закрывать нечем
    for a in sorted(accruals, key=lambda a: (a.due or a.day, a.day, a.kind)):
        take = min(left, a.amount)
        left -= take
        remaining = q(a.amount - take)
        if remaining <= 0:
            continue
        overdue_days = 0 if a.due is None else max((today - a.due).days, 0)
        ledger.items.append(Open(a, remaining, overdue_days))
        ledger.debt += remaining
        if a.due is None:
            ledger.buckets["no_term"] += remaining
        elif overdue_days == 0:
            ledger.not_due += remaining
            ledger.buckets["not_due"] += remaining
            for n in DUE_WINDOWS:
                if (a.due - today).days <= n:
                    ledger.due_in[n] += remaining
        else:
            ledger.overdue += remaining
            for key, upper in OVERDUE_BUCKETS:
                if upper is None or overdue_days <= upper:
                    ledger.buckets[key] += remaining
                    break
    ledger.prepaid = q(left)
    ledger.debt, ledger.overdue, ledger.not_due = q(ledger.debt), q(ledger.overdue), q(ledger.not_due)
    ledger.buckets = {k: q(v) for k, v in ledger.buckets.items()}
    ledger.due_in = {k: q(v) for k, v in ledger.due_in.items()}
    return ledger


def series(accruals: list[Accrual], payments: list[Payment], start: date, end: date) -> list[tuple[date, Decimal]]:
    """Долг по дням на конец дня: начислено до дня включительно минус оплачено до дня включительно. Меньше нуля — аванс
    (график показывает ноль-линию). Дни до `start` входят в начальную сумму."""
    delta: dict[date, Decimal] = defaultdict(Decimal)
    for a in accruals:
        delta[a.day] += a.amount
    for p in payments:
        delta[p.day] -= p.amount
    total = sum((v for d, v in delta.items() if d < start), ZERO)
    out = []
    day = start
    while day <= end:
        total += delta.get(day, ZERO)
        out.append((day, q(total)))
        day += timedelta(days=1)
    return out
