"""ДДС — движение денег за период (шаг 3.10). Чистый модуль — без БД и HTTP.

По статьям и периодам (неделя или месяц): остаток на начало, поступления, платежи, остаток на конец. Переводы между
своими счетами не считаются ни приходом, ни расходом: общий остаток они не меняют. ДДС — по деньгам (когда пришли и
ушли), P&L — по начислениям Ozon: это разные отчёты, их не смешиваем.

Статья относится к поступлениям или платежам по своему виду; операция без статьи — по знаку суммы. Отрицательная сумма
в статье поступлений (возврат, исправление) уменьшает поступления этой статьи, а не попадает в платежи — так итог по
статьям всегда сходится с остатками.

Подстатьи (шаг 10.19): строка статьи — её факт вместе с фактом подстатей, подстатьи — строками внутри неё; итоги по
разделам не меняются (каждая операция считается один раз).

План (шаг 10.24): `forecast` — оставшиеся плановые платежи календаря с сегодня (как в прогнозе 3.9: «ожидается» —
сегодня, у частично оплаченных — остаток): они входят в прогнозный остаток на конец периода; `planned` — план периода
по календарю (все платежи с датой в периоде, кроме пропущенных и выключенных) — для план-факта по статьям, в остатки не
входит. Без плана — ровно как раньше.
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
    closing: Decimal | None  # с прогнозом: факт + оставшийся план периода
    forecast_income: Decimal = ZERO  # оставшиеся плановые поступления
    forecast_expense: Decimal = ZERO
    plan_income: Decimal = ZERO  # план периода по календарю — для план-факта
    plan_expense: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.income + self.expense

    @property
    def forecast_net(self) -> Decimal:
        return self.income + self.expense + self.forecast_income + self.forecast_expense


@dataclass
class Row:
    category: str | None
    section: str  # income | expense
    values: list[Decimal]  # по периодам: факт
    total: Decimal = ZERO
    children: list["Row"] = field(default_factory=list)  # подстатьи — их факт уже в values и total
    forecast: list[Decimal] = field(default_factory=list)  # оставшийся план по периодам
    forecast_total: Decimal = ZERO
    plan: list[Decimal] = field(default_factory=list)  # план периода по календарю
    plan_total: Decimal = ZERO

    def weight(self) -> Decimal:
        return abs(self.total) + abs(self.forecast_total)


@dataclass
class CashFlow:
    periods: list[Period]
    rows: list[Row]
    opening: Decimal | None
    income: Decimal
    expense: Decimal
    closing: Decimal | None  # с прогнозом
    transfers: int = 0  # сколько операций-переводов не вошло в приход и расход
    days_with_flows: set[date] = field(default_factory=set)
    forecast_income: Decimal = ZERO
    forecast_expense: Decimal = ZERO
    plan_income: Decimal = ZERO
    plan_expense: Decimal = ZERO

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


def nest(rows: list[Row], parents: dict[str, str]) -> list[Row]:
    """Подстатьи — внутрь статьи: строка статьи = её факт + факт подстатей (и план так же); статьи без операций, но с
    подстатьями, появляются строкой. Порядок — как у строк: поступления, потом платежи, больше по модулю — выше."""
    top: dict[tuple[str, str | None], Row] = {}
    width = len(rows[0].values) if rows else 0
    for r in rows:
        parent = parents.get(r.category) if r.category else None
        key = (r.section, parent or r.category)
        row = top.get(key)
        if row is None:
            row = top[key] = Row(key[1], r.section, [ZERO] * width, forecast=[ZERO] * width, plan=[ZERO] * width)
        row.values = [a + b for a, b in zip(row.values, r.values)]
        row.forecast = [a + b for a, b in zip(row.forecast, r.forecast or [ZERO] * width)]
        row.plan = [a + b for a, b in zip(row.plan, r.plan or [ZERO] * width)]
        row.total += r.total
        row.forecast_total += r.forecast_total
        row.plan_total += r.plan_total
        if parent is not None:
            row.children.append(r)
    out = sorted(top.values(), key=lambda r: (r.section != "income", -r.weight(), r.category or "~"))
    for r in out:
        r.children.sort(key=lambda c: (-c.weight(), c.category or "~"))
    return out


def cash_flow(opening: Decimal | None, flows: list[Flow], date_from: date, date_to: date, granularity: str = "month",
              parents: dict[str, str] | None = None, forecast: list[Flow] = (), planned: list[Flow] = ()) -> CashFlow:
    """opening — остаток на начало дня date_from по всем счетам (None — неизвестен). flows — операции окна.
    parents — подстатья → статья (ключи статей): подстатьи — строками внутри статьи. forecast — оставшиеся плановые
    платежи (входят в остаток на конец), planned — план периода по календарю (только для план-факта)."""
    periods = buckets(date_from, date_to, granularity)
    n = len(periods)
    index = {}
    for i, (a, b) in enumerate(periods):
        d = a
        while d <= b:
            index[d] = i
            d += timedelta(days=1)
    rows: dict[tuple[str, str | None], Row] = {}
    sums = {kind: {"income": [ZERO] * n, "expense": [ZERO] * n} for kind in ("fact", "forecast", "plan")}
    transfers = 0
    days: set[date] = set()

    def row_for(f: Flow) -> tuple[Row, str]:
        sec = section(f)
        key = (sec, f.category)
        row = rows.get(key)
        if row is None:
            row = rows[key] = Row(f.category, sec, [ZERO] * n, forecast=[ZERO] * n, plan=[ZERO] * n)
        return row, sec

    for f in flows:
        i = index.get(f.day)
        if i is None:
            continue
        if f.transfer:
            transfers += 1
            continue
        row, sec = row_for(f)
        row.values[i] += f.amount
        row.total += f.amount
        sums["fact"][sec][i] += f.amount
        days.add(f.day)
    for kind, items in (("forecast", forecast), ("plan", planned)):
        for f in items:
            i = index.get(f.day)
            if i is None or f.transfer:
                continue
            row, sec = row_for(f)
            getattr(row, kind)[i] += f.amount
            setattr(row, f"{kind}_total", getattr(row, f"{kind}_total") + f.amount)
            sums[kind][sec][i] += f.amount

    q = lambda v: v.quantize(CENT)  # noqa: E731
    out_periods: list[Period] = []
    balance = opening
    for i, (a, b) in enumerate(periods):
        start = balance
        move = sums["fact"]["income"][i] + sums["fact"]["expense"][i] + sums["forecast"]["income"][i] + sums["forecast"]["expense"][i]
        if balance is not None:
            balance = q(balance + move)
        out_periods.append(Period(
            a, b, None if start is None else q(start), q(sums["fact"]["income"][i]), q(sums["fact"]["expense"][i]),
            balance, q(sums["forecast"]["income"][i]), q(sums["forecast"]["expense"][i]),
            q(sums["plan"]["income"][i]), q(sums["plan"]["expense"][i]),
        ))
    ordered = sorted(rows.values(), key=lambda r: (r.section != "income", -r.weight(), r.category or "~"))
    if parents:
        ordered = nest(ordered, parents)

    def tidy(r: Row) -> None:
        r.values, r.forecast, r.plan = [q(v) for v in r.values], [q(v) for v in r.forecast], [q(v) for v in r.plan]
        r.total, r.forecast_total, r.plan_total = q(r.total), q(r.forecast_total), q(r.plan_total)
        for c in r.children:
            tidy(c)

    for r in ordered:
        tidy(r)
    return CashFlow(
        periods=out_periods, rows=ordered, opening=None if opening is None else q(opening),
        income=q(sum(sums["fact"]["income"], ZERO)), expense=q(sum(sums["fact"]["expense"], ZERO)), closing=balance,
        transfers=transfers, days_with_flows=days,
        forecast_income=q(sum(sums["forecast"]["income"], ZERO)), forecast_expense=q(sum(sums["forecast"]["expense"], ZERO)),
        plan_income=q(sum(sums["plan"]["income"], ZERO)), plan_expense=q(sum(sums["plan"]["expense"], ZERO)),
    )
