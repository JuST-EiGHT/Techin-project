"""ДДС — движение денег за период (шаг 3.10). Чистый модуль — без БД и HTTP.

По статьям и периодам (неделя или месяц): остаток на начало, поступления, платежи, остаток на конец. Переводы между
своими счетами не считаются ни приходом, ни расходом: общий остаток они не меняют. ДДС — по деньгам (когда пришли и
ушли), P&L — по начислениям Ozon: это разные отчёты, их не смешиваем.

Статья относится к поступлениям или платежам по своему виду; операция без статьи — по знаку суммы. Отрицательная сумма
в статье поступлений (возврат, исправление) уменьшает поступления этой статьи, а не попадает в платежи — так итог по
статьям всегда сходится с остатками.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Flow:
    """Операция-факт: + приход, − расход."""

    day: date
    amount: Decimal
    category: str | None = None  # ключ статьи; None — без статьи
    kind: str | None = None  # вид статьи income | expense; None — без статьи
    transfer: bool = False


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    opening: Decimal | None  # None — остаток на начало неизвестен
    income: Decimal
    expense: Decimal  # отрицательное число
    closing: Decimal | None

    @property
    def net(self) -> Decimal:
        return self.income + self.expense


@dataclass
class Row:
    category: str | None
    section: str  # income | expense
    values: list[Decimal]  # по периодам
    total: Decimal = ZERO


@dataclass
class CashFlow:
    periods: list[Period]
    rows: list[Row]
    opening: Decimal | None
    income: Decimal
    expense: Decimal
    closing: Decimal | None
    transfers: int = 0  # сколько операций-переводов не вошло в приход и расход
    days_with_flows: set[date] = field(default_factory=set)

    @property
    def net(self) -> Decimal:
        return self.income + self.expense


def buckets(date_from: date, date_to: date, granularity: str) -> list[tuple[date, date]]:
    """Периоды: недели с понедельника или календарные месяцы; первый и последний обрезаны по окну."""
    out: list[tuple[date, date]] = []
    start = date_from
    while start <= date_to:
        if granularity == "week":
            end = start + timedelta(days=6 - start.weekday())
        else:
            end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        end = min(end, date_to)
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def section(flow: Flow) -> str:
    if flow.kind in ("income", "expense"):
        return flow.kind
    return "income" if flow.amount > 0 else "expense"


def cash_flow(opening: Decimal | None, flows: list[Flow], date_from: date, date_to: date, granularity: str = "month") -> CashFlow:
    """opening — остаток на начало дня date_from по всем счетам (None — неизвестен). flows — операции окна."""
    periods = buckets(date_from, date_to, granularity)
    index = {}
    for i, (a, b) in enumerate(periods):
        d = a
        while d <= b:
            index[d] = i
            d += timedelta(days=1)
    rows: dict[tuple[str, str | None], Row] = {}
    income = [ZERO] * len(periods)
    expense = [ZERO] * len(periods)
    transfers = 0
    days: set[date] = set()
    for f in flows:
        i = index.get(f.day)
        if i is None:
            continue
        if f.transfer:
            transfers += 1
            continue
        sec = section(f)
        key = (sec, f.category)
        row = rows.get(key)
        if row is None:
            row = rows[key] = Row(f.category, sec, [ZERO] * len(periods))
        row.values[i] += f.amount
        row.total += f.amount
        (income if sec == "income" else expense)[i] += f.amount
        days.add(f.day)

    out_periods: list[Period] = []
    balance = opening
    for i, (a, b) in enumerate(periods):
        start = balance
        if balance is not None:
            balance = (balance + income[i] + expense[i]).quantize(CENT)
        out_periods.append(Period(a, b, None if start is None else start.quantize(CENT), income[i].quantize(CENT),
                                  expense[i].quantize(CENT), balance))
    ordered = sorted(rows.values(), key=lambda r: (r.section != "income", -abs(r.total), r.category or "~"))
    for r in ordered:
        r.values = [v.quantize(CENT) for v in r.values]
        r.total = r.total.quantize(CENT)
    total_in = sum(income, ZERO).quantize(CENT)
    total_out = sum(expense, ZERO).quantize(CENT)
    return CashFlow(
        periods=out_periods, rows=ordered, opening=None if opening is None else opening.quantize(CENT),
        income=total_in, expense=total_out, closing=balance, transfers=transfers, days_with_flows=days,
    )
