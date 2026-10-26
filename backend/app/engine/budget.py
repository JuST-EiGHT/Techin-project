"""Бюджеты и сценарии (шаг 10.21). Чистый модуль — без БД.

План — сумма по статье на месяц (≥ 0, знак — по виду статьи); квартал и год — суммы месяцев. Нет плана ни в одном
месяце периода — плана нет (не 0). План статьи с подстатьями — её свой план + планы подстатей; факт — так же (как ДДС).

Факт — по деньгам (как ДДС 3.10): поступления статьи — сумма её операций; расходы — минус сумма (возврат по статье
расходов уменьшает расход). Переводы между своими счетами не считаются. Период ещё не начался — факта нет.

Отклонение = факт − план; % = отклонение / план. Хуже плана: доход меньше, расход больше.
Сценарии — множители к плановым поступлениям и платежам: базовый ×1, оптимистичный и пессимистичный — на сколько %
больше или меньше (настройка).
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь",
          "декабрь"]
SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    index = d.year * 12 + d.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def month_end(d: date) -> date:
    return add_months(month_start(d), 1) - timedelta(days=1)


@dataclass(frozen=True)
class Period:
    key: str  # 2026-03 | 2026-Q1 | 2026
    label: str
    months: tuple[date, ...]  # первые числа месяцев

    @property
    def start(self) -> date:
        return self.months[0]

    @property
    def end(self) -> date:
        return month_end(self.months[-1])


def periods(year: int, granularity: str) -> list[Period]:
    """Месяцы, кварталы или год целиком."""
    months = [date(year, m, 1) for m in range(1, 13)]
    if granularity == "month":
        return [Period(f"{year}-{m.month:02d}", f"{SHORT[m.month - 1]} {year}", (m,)) for m in months]
    if granularity == "quarter":
        return [Period(f"{year}-Q{q + 1}", f"{q + 1} кв. {year}", tuple(months[q * 3:q * 3 + 3])) for q in range(4)]
    return [Period(str(year), f"{year} год", tuple(months))]


def sum_known(values) -> Decimal | None:
    """Сумма известных; ни одного — None (нет данных, не 0)."""
    known = [v for v in values if v is not None]
    return sum(known, ZERO) if known else None


def group(own: Decimal | None, children: list[Decimal | None]) -> Decimal | None:
    """Статья с подстатьями: свой план (или факт) + подстатей; нет ни одного — None."""
    return sum_known([own, *children])


@dataclass
class Cell:
    plan: Decimal | None  # None — плана нет
    fact: Decimal | None  # None — период ещё не начался
    diff: Decimal | None  # факт − план
    pct: Decimal | None  # отклонение в % от плана
    worse: bool | None  # хуже плана: поступлений меньше или расходов больше


def cell(plan: Decimal | None, fact: Decimal | None, kind: str) -> Cell:
    if plan is None or fact is None:
        return Cell(plan, fact, None, None, None)
    diff = (fact - plan).quantize(CENT)
    pct = (diff / plan * 100).quantize(Decimal("0.1"), ROUND_HALF_UP) if plan else None
    worse = diff < 0 if kind == "income" else diff > 0
    return Cell(plan, fact, diff, pct, worse if diff != 0 else False)


def split(amount: Decimal, n: int) -> list[Decimal]:
    """Сумма квартала или года поровну по месяцам до копейки; остаток — в последний месяц."""
    part = (amount / n).quantize(CENT, rounding=ROUND_DOWN)
    return [part] * (n - 1) + [amount - part * (n - 1)]


@dataclass(frozen=True)
class Scenario:
    key: str  # base | optimistic | pessimistic
    label: str
    income_pct: Decimal  # на сколько % поступления больше (+) или меньше (−) плана
    expense_pct: Decimal

    def apply(self, amount: Decimal) -> Decimal:
        """Сумма со знаком: + поступление, − платёж."""
        pct = self.income_pct if amount > 0 else self.expense_pct
        return (amount * (1 + pct / 100)).quantize(CENT, ROUND_HALF_UP)


def scenarios(opt_income: Decimal, opt_expense: Decimal, pes_income: Decimal, pes_expense: Decimal) -> list[Scenario]:
    return [
        Scenario("base", "Базовый", ZERO, ZERO),
        Scenario("optimistic", "Оптимистичный", Decimal(opt_income), Decimal(opt_expense)),
        Scenario("pessimistic", "Пессимистичный", Decimal(pes_income), Decimal(pes_expense)),
    ]


def remainder(plan: Decimal | None, fact: Decimal | None, scheduled: Decimal) -> Decimal:
    """Сколько бюджета статьи на месяц ещё не видно ни в факте, ни в платежах календаря — его добавляем в прогноз."""
    if plan is None:
        return ZERO
    return max(plan - max(fact or ZERO, ZERO) - scheduled, ZERO)
