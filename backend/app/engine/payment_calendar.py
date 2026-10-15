"""Платёжный календарь (шаг 3.9). Чистый модуль — без БД и HTTP.

- `occurrences` — даты регулярного платежа в окне: раз в неделю / месяц / квартал / год, с начала и до конца плана;
- `apply_overrides` — перенос, своя сумма, пропуск отдельного платежа;
- `match` — план-факт: платёж закрывает отмеченная операция («оплачено») или подходящая операция (та же сторона,
  сумма ± допуск, дата ± N дней, контрагент и статья, если указаны в плане); прогноз выплаты Ozon закрывает
  фактическая выплата Ozon в тот же день;
- `forecast` — остаток по дням = остаток сейчас + плановые поступления − плановые платежи; кассовый разрыв — дни ниже
  нуля или порога. В прогнозе только действующие платежи с сегодняшнего дня: выключенный план не участвует («что
  если»), неоплаченный прошлый платёж — тоже (его показываем отдельно: перенести или отметить).
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
STEP_MONTHS = {"month": 1, "quarter": 3, "year": 12}
MATCH_PCT = Decimal(10)  # сумма операции может отличаться от плана на 10 %
MATCH_DAYS = 3  # и дата — на 3 дня в любую сторону


@dataclass(frozen=True)
class Rule:
    start: date
    period: str = "once"  # once | week | month | quarter | year
    day: int | None = None  # день недели 1–7 или число месяца 1–31; None — как у даты начала
    end: date | None = None


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + n
    return index // 12, index % 12 + 1


def occurrences(rule: Rule, date_from: date, date_to: date) -> list[date]:
    """Даты платежа в окне [date_from, date_to]. Число больше длины месяца — последний день месяца."""
    end = min(date_to, rule.end) if rule.end else date_to
    if rule.period == "once":
        return [rule.start] if date_from <= rule.start <= end else []
    lo = max(date_from, rule.start)
    if lo > end:
        return []
    if rule.period == "week":
        weekday = rule.day or rule.start.isoweekday()
        first = lo + timedelta(days=(weekday - lo.isoweekday()) % 7)
        return [first + timedelta(weeks=k) for k in range((end - first).days // 7 + 1)] if first <= end else []
    step = STEP_MONTHS[rule.period]
    day = rule.day or rule.start.day
    months_to_lo = (lo.year - rule.start.year) * 12 + lo.month - rule.start.month
    k = max(0, months_to_lo // step - 1)
    out: list[date] = []
    while True:
        y, m = add_months(rule.start.year, rule.start.month, k * step)
        d = _month_day(y, m, day)
        if d > end:
            return out
        if d >= lo:
            out.append(d)
        k += 1


@dataclass(frozen=True)
class Override:
    day: date | None = None  # перенесён на эту дату
    amount: Decimal | None = None  # своя сумма, > 0
    skipped: bool = False
    op_id: str | None = None  # оплачено: операция-факт
    paid_day: date | None = None  # оплачено без операции (планирование не включено): дата и сумма — факт
    paid_amount: Decimal | None = None


@dataclass(frozen=True)
class Op:
    """Операция-факт (money_operations): + приход, − расход."""

    op_id: str
    day: date
    amount: Decimal
    source: str = "manual"
    category_id: str | None = None
    counterparty: str | None = None


@dataclass
class Item:
    key: str
    source: str  # ozon | manual | supplier | tax
    title: str
    planned_day: date  # по плану — до переноса
    amount: Decimal | None  # со знаком: + поступление, − платёж; None — сумма пока неизвестна
    kind: str = "expense"  # income | expense — сторона, даже когда суммы ещё нет
    enabled: bool = True
    estimate: bool = False  # сумма — оценка (прогноз Ozon, продажи вперёд, налог по неполному периоду)
    category_id: str | None = None
    counterparty: str | None = None
    meta: dict = field(default_factory=dict)  # для ответа API: план, счёт, статья, пояснение, ссылка на расчёт
    day: date | None = None  # после переноса; None — как по плану
    plan_amount: Decimal | None = None  # сумма по плану — до своей суммы
    moved: bool = False
    own_amount: bool = False
    skipped: bool = False
    fact: Op | None = None
    fact_marked: bool = False  # отмечено «оплачено», а не найдено по сумме и дате

    def __post_init__(self) -> None:
        self.day = self.day or self.planned_day
        self.plan_amount = self.amount

    def status(self, today: date) -> str:
        if self.skipped:
            return "skipped"
        if self.fact is not None:
            return "paid"
        if not self.enabled:
            return "disabled"
        return "overdue" if self.day < today else "planned"

    @property
    def diff(self) -> Decimal | None:
        """Факт − план; 0 — совпали."""
        if self.fact is None or self.amount is None:
            return None
        return (self.fact.amount - self.amount).quantize(CENT)


def apply_overrides(items: list[Item], overrides: dict[str, Override]) -> None:
    for item in items:
        o = overrides.get(item.key)
        if o is None:
            continue
        if o.day is not None and o.day != item.planned_day:
            item.day, item.moved = o.day, True
        if o.amount is not None:
            sign = 1 if item.kind == "income" else -1
            item.amount, item.own_amount = sign * o.amount, True
        item.skipped = o.skipped


def _words(text: str | None) -> set[str]:
    clean = "".join(ch if ch.isalnum() else " " for ch in (text or "").lower().replace("ё", "е"))
    return {w for w in clean.split() if w}


def _same_text(a: str | None, b: str | None) -> bool:
    """Контрагент тот же: слова одного названия есть в другом — «ИП Петров» = «Петров ИП», «ООО Ромашка» = «Ромашка»."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def _fits(item: Item, op: Op, pct: Decimal, days: int) -> bool:
    if item.amount is None or item.amount == 0 or (op.amount > 0) != (item.kind == "income"):
        return False
    if abs(abs(op.amount) - abs(item.amount)) > abs(item.amount) * pct / 100:
        return False
    if abs((op.day - item.day).days) > days:
        return False
    if item.counterparty and not _same_text(item.counterparty, op.counterparty):
        return False
    return not (item.category_id and op.category_id != item.category_id)


def match(
    items: list[Item], ops: list[Op], overrides: dict[str, Override], pct: Decimal = MATCH_PCT, days: int = MATCH_DAYS
) -> None:
    """Закрыть платежи фактом. Отмеченные «оплачено» — своей операцией (или датой и суммой отметки, если операций
    нет). Остальные — по сумме и дате: каждая операция закрывает не больше одного платежа, ближайший по дате.
    Выплаты Ozon — только выплатой Ozon в тот же день."""
    by_id = {op.op_id: op for op in ops}
    used: set[str] = set()
    for item in items:
        o = overrides.get(item.key)
        if o is None:
            continue
        if o.op_id is not None and o.op_id in by_id:
            item.fact, item.fact_marked = by_id[o.op_id], True
            used.add(o.op_id)
        elif o.op_id is None and o.paid_day is not None and o.paid_amount is not None:
            sign = 1 if item.kind == "income" else -1
            item.fact = Op("", o.paid_day, sign * o.paid_amount, source="mark")
            item.fact_marked = True
    payouts = {op.day: op for op in ops if op.source == "ozon_payout"}
    free = [op for op in ops if op.op_id not in used and op.source != "ozon_payout"]
    for item in sorted(items, key=lambda i: (i.day, i.key)):
        if item.fact is not None or item.skipped:
            continue
        if item.source == "ozon":
            if (payout := payouts.get(item.day)) is not None:
                item.fact = payout
            continue
        candidates = [op for op in free if op.op_id not in used and _fits(item, op, pct, days)]
        if not candidates:
            continue
        best = min(candidates, key=lambda op: (abs((op.day - item.day).days), abs(abs(op.amount) - abs(item.amount)), op.op_id))
        item.fact = best
        used.add(best.op_id)


TAX_EARLY_DAYS = 40  # ЕНП платят заранее: платёж за 40 дней до срока ещё относится к нему
TAX_LATE_DAYS = 10  # и с опозданием до 10 дней


def close_taxes(items: list[Item], ops: list[Op], tax_categories: set[str]) -> None:
    """ЕНП (шаг 3.12): платёж со статьёй «Налоги и взносы» закрывает плановые налоги с ближайшим сроком — даже если
    сумма другая (налог в плане — оценка). Одним платежом ЕНП платят сразу несколько налогов с одним сроком — он
    закрывает их все (`meta["shared_fact"]`: разница с планом по отдельному налогу тогда не считается).
    Вызывать после `match`: точные совпадения по сумме и дате уже разобраны."""
    used = matched_ops(items)
    open_items = [i for i in items if i.source == "tax" and i.fact is None and not i.skipped]
    if not open_items:
        return
    for op in sorted(ops, key=lambda o: (o.day, o.op_id)):
        if op.category_id not in tax_categories or op.amount >= 0 or op.op_id in used or op.source == "mark":
            continue
        dues = sorted({i.day for i in open_items
                       if i.fact is None and op.day - timedelta(days=TAX_LATE_DAYS) <= i.day <= op.day + timedelta(days=TAX_EARLY_DAYS)})
        if not dues:
            continue
        group = [i for i in open_items if i.fact is None and i.day == dues[0]]
        for item in group:
            item.fact = op
            item.meta["shared_fact"] = len(group) > 1
        used.add(op.op_id)


def matched_ops(items: list[Item]) -> set[str]:
    return {i.fact.op_id for i in items if i.fact is not None}


@dataclass
class Day:
    day: date
    income: Decimal
    expense: Decimal
    balance: Decimal | None  # None — остаток сейчас неизвестен (не указан ни у одного счёта)


@dataclass
class Gap:
    date_from: date
    date_to: date
    min_balance: Decimal
    min_day: date
    below_zero: bool  # ниже нуля — денег не хватит; иначе только ниже порога


@dataclass
class Forecast:
    days: list[Day]
    gaps: list[Gap]
    first_below_zero: date | None  # первый день ниже нуля; None — денег хватит на весь горизонт
    first_below_threshold: date | None
    min_balance: Decimal | None
    end_balance: Decimal | None


def in_forecast(item: Item, today: date) -> bool:
    return item.status(today) == "planned" and item.amount is not None


def forecast(start: Decimal | None, items: list[Item], today: date, date_to: date, threshold: Decimal | None = None) -> Forecast:
    """Остаток на конец каждого дня с сегодня по date_to. start — остаток сейчас (с сегодняшними операциями)."""
    flows: dict[date, list[Decimal]] = {}
    for item in items:
        if in_forecast(item, today) and today <= item.day <= date_to:
            flows.setdefault(item.day, []).append(item.amount)
    limit = threshold if threshold is not None else ZERO
    days: list[Day] = []
    balance = start
    for n in range((date_to - today).days + 1):
        d = today + timedelta(days=n)
        values = flows.get(d, [])
        income = sum((v for v in values if v > 0), ZERO)
        expense = sum((v for v in values if v < 0), ZERO)
        if balance is not None:
            balance = (balance + income + expense).quantize(CENT)
        days.append(Day(d, income.quantize(CENT), expense.quantize(CENT), balance))

    gaps: list[Gap] = []
    if start is not None:
        run: list[Day] = []
        for day in [*days, None]:
            if day is not None and day.balance < limit:
                run.append(day)
                continue
            if run:
                low = min(run, key=lambda x: (x.balance, x.day))
                gaps.append(Gap(run[0].day, run[-1].day, low.balance, low.day, any(x.balance < 0 for x in run)))
                run = []
    known = [d for d in days if d.balance is not None]
    return Forecast(
        days=days,
        gaps=gaps,
        first_below_zero=next((d.day for d in known if d.balance < 0), None),
        first_below_threshold=next((d.day for d in known if d.balance < limit), None),
        min_balance=min((d.balance for d in known), default=None),
        end_balance=known[-1].balance if known else None,
    )
