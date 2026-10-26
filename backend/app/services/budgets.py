"""Бюджеты и сценарии (шаг 10.21): план по статьям на месяц, план-факт по месяцам, кварталам и году, заполнение,
сценарии и прогноз денег с риском кассового разрыва.

Статьи бюджета — поступлений и расходов с отметкой «бюджеты» (`in_budget`, шаг 10.19). План — `budget_lines` с версиями
(правило 12). Факт — операции (как ДДС 3.10, `engine/budget`). Итог раздела по факту — все операции раздела, как в ДДС:
что не в статьях бюджета и без статьи — строкой «Вне бюджета».

Прогноз по сценарию — расчёт 3.10 (`payment_calendar.forecast`): остаток сейчас + платежи календаря + остаток бюджета,
который ещё не виден ни в факте, ни в календаре (расход — в начале месяца или сегодня, поступление — в конце месяца:
так риск разрыва виден раньше), × множители сценария.
"""
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import budget as eng
from app.engine import payment_calendar as cal
from app.models.planning import BUDGET_SOURCES, BudgetLine, CashCategory, MoneyOperation
from app.services import plan_calendar
from app.services import planning as planning_svc
from app.services.planning import MAX_AMOUNT, PlanningError, _current
from app.services.sync.finance import today_msk

ZERO = Decimal(0)
CENT = Decimal("0.01")
KINDS = ("income", "expense")
FILL_SOURCES = ("last_year", "prev_period")


def _now():
    return planning_svc._now()


# ---------- данные ----------


async def current_lines(db: AsyncSession, user_id: uuid.UUID, lo: date, hi: date) -> dict[tuple[uuid.UUID, date], BudgetLine]:
    res = await db.execute(select(BudgetLine).where(
        BudgetLine.user_id == user_id, BudgetLine.month >= lo, BudgetLine.month <= hi, BudgetLine.superseded_at.is_(None)))
    return {(r.category_id, r.month): r for r in res.scalars()}


def _plans(lines: dict) -> dict[tuple[uuid.UUID, date], Decimal]:
    return {k: Decimal(v.amount) for k, v in lines.items() if v.amount is not None}


async def month_facts(db: AsyncSession, user_id: uuid.UUID, lo: date, hi: date, cats: dict) -> dict[tuple, Decimal]:
    """Факт по статье и месяцу (≥ 0 — по виду статьи): поступления — сумма, расходы — минус сумма. Без статьи — ключ
    (None, вид): по знаку суммы. Переводы не считаются."""
    rows = (await db.execute(_current(select(MoneyOperation.category_id, MoneyOperation.day, MoneyOperation.amount).where(
        MoneyOperation.user_id == user_id, MoneyOperation.transfer_id.is_(None),
        MoneyOperation.day >= lo, MoneyOperation.day <= hi)))).all()
    out: dict[tuple, Decimal] = {}
    for cat_id, day, amount in rows:
        cat = cats.get(cat_id)
        kind = cat.kind if cat is not None and cat.kind in KINDS else ("income" if amount > 0 else "expense")
        key = (cat_id if cat is not None and cat.kind in KINDS else (None, kind), eng.month_start(day))
        value = Decimal(amount) if kind == "income" else -Decimal(amount)
        out[key] = out.get(key, ZERO) + value
    return out


def _budget_cats(cats: dict[uuid.UUID, CashCategory], keep: set[uuid.UUID]) -> list[CashCategory]:
    """Статьи бюджета: с отметкой «бюджеты», действующие — или с планом / фактом в периоде (их не прячем)."""
    return [c for c in cats.values() if c.kind in KINDS and c.in_budget and (c.archived_at is None or c.id in keep)]


def _tree(items: list[CashCategory]) -> list[tuple[CashCategory, list[CashCategory]]]:
    ids = {c.id for c in items}
    tops = [c for c in items if not c.parent_id or c.parent_id not in ids]
    return [(c, [x for x in items if x.parent_id == c.id]) for c in tops]


def _cell_out(c: eng.Cell) -> dict:
    q = (lambda v: None if v is None else Decimal(v).quantize(CENT))
    return {"plan": q(c.plan), "fact": q(c.fact), "diff": c.diff, "pct": c.pct, "worse": c.worse}


# ---------- план-факт ----------


async def view(db: AsyncSession, user_id: uuid.UUID, year: int, granularity: str) -> dict:
    today = today_msk()
    ps = eng.periods(year, granularity)
    year_months = [date(year, m, 1) for m in range(1, 13)]
    lo, hi = year_months[0], eng.month_end(year_months[-1])
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    lines = await current_lines(db, user_id, lo, year_months[-1])
    plans = _plans(lines)
    facts = await month_facts(db, user_id, lo, min(hi, today), cats)
    started = {m for m in year_months if m <= today}
    keep = {k[0] for k in plans} | {k[0] for k in facts if isinstance(k[0], uuid.UUID)}
    tree = _tree(_budget_cats(cats, keep))

    def own_plan(cid, m):
        return plans.get((cid, m))

    def own_fact(cid, m):
        return facts.get((cid, m), ZERO) if m in started else None

    def months_cell(kind, plan_of, fact_of, months) -> eng.Cell:
        plan = eng.sum_known(plan_of(m) for m in months)
        fact = eng.sum_known(fact_of(m) for m in months)
        return eng.cell(plan, fact, kind)

    def row(cat: CashCategory, children: list[CashCategory]) -> dict:
        def plan_of(m):
            return eng.group(own_plan(cat.id, m), [own_plan(ch.id, m) for ch in children])

        def fact_of(m):
            return eng.group(own_fact(cat.id, m), [own_fact(ch.id, m) for ch in children])

        return {
            "category": {"id": cat.id, "name": cat.name, "kind": cat.kind, "parent_id": cat.parent_id,
                         "archived": cat.archived_at is not None},
            "own": [{"month": m, "plan": own_plan(cat.id, m)} for m in year_months],
            "cells": [_cell_out(months_cell(cat.kind, plan_of, fact_of, p.months)) for p in ps],
            "total": _cell_out(months_cell(cat.kind, plan_of, fact_of, year_months)),
            "children": [row(ch, []) for ch in children],
            "plan_of": plan_of, "fact_of": fact_of,
        }

    sections = {}
    for kind in KINDS:
        rows = [row(c, ch) for c, ch in tree if c.kind == kind]
        in_rows = {c.id for c, ch in tree if c.kind == kind} | {x.id for c, ch in tree if c.kind == kind for x in ch}

        def total_fact(m, kind=kind):
            if m not in started:
                return None
            return sum((v for (k, mm), v in facts.items() if mm == m and (
                (isinstance(k, uuid.UUID) and cats[k].kind == kind) or (isinstance(k, tuple) and k[1] == kind))), ZERO)

        def total_plan(m, rows=rows):
            return eng.sum_known(r["plan_of"](m) for r in rows)

        def other_fact(m, kind=kind, in_rows=in_rows):
            if m not in started:
                return None
            return sum((v for (k, mm), v in facts.items() if mm == m and k not in in_rows and (
                (isinstance(k, uuid.UUID) and cats[k].kind == kind) or (isinstance(k, tuple) and k[1] == kind))), ZERO)

        other = {"cells": [_cell_out(months_cell(kind, lambda m: None, other_fact, p.months)) for p in ps],
                 "total": _cell_out(months_cell(kind, lambda m: None, other_fact, year_months))}
        sections[kind] = {
            "rows": rows, "other": other,
            "cells": [_cell_out(months_cell(kind, total_plan, total_fact, p.months)) for p in ps],
            "total": _cell_out(months_cell(kind, total_plan, total_fact, year_months)),
            "plan_of": total_plan, "fact_of": total_fact,
        }

    def net(months) -> dict:
        inc, exp = sections["income"], sections["expense"]
        pi, pe = (eng.sum_known(s["plan_of"](m) for m in months) for s in (inc, exp))
        fi, fe = (eng.sum_known(s["fact_of"](m) for m in months) for s in (inc, exp))
        plan = None if pi is None and pe is None else (pi or ZERO) - (pe or ZERO)
        fact = None if fi is None and fe is None else (fi or ZERO) - (fe or ZERO)
        return _cell_out(eng.cell(plan, fact, "income"))

    def strip(r: dict) -> dict:
        return {k: (v if k != "children" else [strip(x) for x in v]) for k, v in r.items() if k not in ("plan_of", "fact_of")}

    return {
        "year": year, "granularity": granularity, "today": today,
        "periods": [{"key": p.key, "label": p.label, "date_from": p.start, "date_to": p.end,
                     "started": p.start <= today, "partial": p.start <= today <= p.end} for p in ps],
        "income": {"rows": [strip(r) for r in sections["income"]["rows"]], "other": sections["income"]["other"],
                   "cells": sections["income"]["cells"], "total": sections["income"]["total"]},
        "expense": {"rows": [strip(r) for r in sections["expense"]["rows"]], "other": sections["expense"]["other"],
                    "cells": sections["expense"]["cells"], "total": sections["expense"]["total"]},
        "net": {"cells": [net(p.months) for p in ps], "total": net(year_months)},
        "has_plan": bool(plans),
    }


# ---------- правка ----------


async def _budget_category(db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID) -> CashCategory:
    cat = await planning_svc._category(db, user_id, category_id)
    if cat.kind not in KINDS:
        raise PlanningError("Бюджет — по статьям поступлений и расходов; переводы между своими счетами не планируются")
    if not cat.in_budget:
        raise PlanningError(f"Статья «{cat.name}» не участвует в бюджетах — отметьте «Бюджеты» в «Статьях»")
    if cat.archived_at is not None:
        raise PlanningError(f"Статья «{cat.name}» в архиве — верните её, чтобы планировать")
    return cat


def period_months(granularity: str, start: date) -> list[date]:
    first = eng.month_start(start)
    if granularity == "quarter":
        first = date(first.year, (first.month - 1) // 3 * 3 + 1, 1)
        return [eng.add_months(first, i) for i in range(3)]
    if granularity == "year":
        return [date(first.year, m, 1) for m in range(1, 13)]
    return [first]


def _check_amount(amount: Decimal | None) -> Decimal | None:
    if amount is None:
        return None
    if amount < 0:
        raise PlanningError("План — число не меньше нуля: поступление или расход — по виду статьи")
    if amount >= MAX_AMOUNT:
        raise PlanningError("План: слишком большое число — проверьте")
    return Decimal(amount).quantize(CENT)


async def write(db: AsyncSession, user_id: uuid.UUID, values: dict[tuple[uuid.UUID, date], Decimal | None],
                source: str) -> int:
    """Новые значения плана: изменилось — новая строка, прежняя `superseded_at`. Возвращает число изменённых месяцев."""
    assert source in BUDGET_SOURCES
    if not values:
        return 0
    months = [m for _, m in values]
    current = await current_lines(db, user_id, min(months), max(months))
    changed = 0
    for (cat_id, month), value in values.items():
        line = current.get((cat_id, month))
        before = None if line is None or line.amount is None else Decimal(line.amount)
        if before == value or (line is None and value is None):
            continue
        if line is not None:
            line.superseded_at = _now()
        db.add(BudgetLine(user_id=user_id, category_id=cat_id, month=month, amount=value, source=source,
                          created_at=_now()))
        changed += 1
    await db.flush()
    return changed


async def set_plan(db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID, granularity: str, start: date,
                   amount: Decimal | None) -> int:
    """План статьи на месяц, квартал или год. Квартал и год — поровну по месяцам (остаток копеек — в последний);
    пусто — план убрать."""
    cat = await _budget_category(db, user_id, category_id)
    amount = _check_amount(amount)
    months = period_months(granularity, start)
    parts = eng.split(amount, len(months)) if amount is not None else [None] * len(months)
    return await write(db, user_id, {(cat.id, m): v for m, v in zip(months, parts)}, "manual")


async def history(db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID, month: date) -> list[dict]:
    await planning_svc._category(db, user_id, category_id)
    res = await db.execute(select(BudgetLine).where(
        BudgetLine.user_id == user_id, BudgetLine.category_id == category_id, BudgetLine.month == eng.month_start(month),
    ).order_by(BudgetLine.created_at.desc()))
    return [{"amount": r.amount, "source": r.source, "created_at": r.created_at, "superseded_at": r.superseded_at}
            for r in res.scalars()]


async def fill(db: AsyncSession, user_id: uuid.UUID, source: str, granularity: str, start: date, overwrite: bool,
               dry_run: bool) -> dict:
    """План периода по факту: «как в прошлом году» — тот же месяц год назад, «как прошлый период» — месяцы периода той
    же длины перед ним. Свой факт статьи (без подстатей — у них свой план). Факта нет или он 0 — не заполняем (нет
    данных ≠ 0); уже есть план — не трогаем, если не попросили заменить."""
    if source not in FILL_SOURCES:
        raise PlanningError("Заполнить можно «как в прошлом году» или «как прошлый период»")
    months = period_months(granularity, start)
    shift = 12 if source == "last_year" else len(months)
    origin = {m: eng.add_months(m, -shift) for m in months}
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    facts = await month_facts(db, user_id, min(origin.values()), eng.month_end(max(origin.values())), cats)
    plans = _plans(await current_lines(db, user_id, months[0], months[-1]))
    targets = [c for c in _budget_cats(cats, set()) if c.archived_at is None]
    values: dict[tuple, Decimal] = {}
    kept = 0
    for c in targets:
        for m in months:
            value = facts.get((c.id, origin[m]))
            if value is None or value <= 0:
                continue
            value = value.quantize(CENT)
            current = plans.get((c.id, m))
            if current is not None and not overwrite:
                kept += current != value
                continue
            if current != value:
                values[(c.id, m)] = value
    examples = [{"category": cats[cid].name, "month": m, "before": plans.get((cid, m)), "after": v}
                for (cid, m), v in sorted(values.items(), key=lambda kv: (kv[0][1], cats[kv[0][0]].name))[:12]]
    changed = len(values) if dry_run else await write(db, user_id, values, source)
    return {"changed": changed, "kept": kept, "examples": examples,
            "from": min(origin.values()), "to": eng.month_end(max(origin.values()))}


# ---------- сценарии и прогноз ----------


async def scenario_settings(db: AsyncSession, user_id: uuid.UUID) -> list[eng.Scenario]:
    s = await planning_svc.get_settings(db, user_id)
    if s is None:
        return eng.scenarios(Decimal(10), Decimal(-5), Decimal(-10), Decimal(10))
    return eng.scenarios(s.scen_opt_income, s.scen_opt_expense, s.scen_pes_income, s.scen_pes_expense)


async def update_scenarios(db: AsyncSession, user_id: uuid.UUID, changes: dict) -> None:
    s = await planning_svc.get_settings(db, user_id)
    if s is None:
        raise PlanningError("Сначала включите «Планирование»")
    for key, value in changes.items():
        if value is not None:
            setattr(s, key, Decimal(value).quantize(Decimal("0.1")))
    await db.flush()


async def forecast(db: AsyncSession, user_id: uuid.UUID, months: int) -> dict:
    today = today_msk()
    first = eng.month_start(today)
    horizon = eng.month_end(eng.add_months(first, months - 1))
    snap = await plan_calendar.snapshot(db, user_id, today, horizon)
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    by_code = {c.code: c.id for c in cats.values() if c.code and c.archived_at is None}

    def category_of(item: cal.Item) -> uuid.UUID | None:
        if item.category_id:
            return uuid.UUID(item.category_id)
        return by_code.get(plan_calendar.TAX_CATEGORY.get(item.source))

    # платежи календаря в прогнозе: день и сумма — как у прогноза (ожидаемые — сегодня, частично оплаченные — остаток)
    planned = []
    for i in snap.items:
        flow = cal.forecast_flow(i, today)
        if flow is not None and flow[0] <= horizon:
            planned.append(cal.Item(key=i.key, source=i.source, title=i.title, planned_day=flow[0], amount=flow[1],
                                    kind=i.kind, category_id=i.category_id))
    scheduled: dict[tuple, Decimal] = {}
    for i in planned:
        key = (category_of(i), eng.month_start(i.day))
        scheduled[key] = scheduled.get(key, ZERO) + abs(i.amount)
    plans = _plans(await current_lines(db, user_id, first, eng.month_start(horizon)))
    facts = await month_facts(db, user_id, first, today, cats)
    added: list[cal.Item] = []
    for (cid, m), plan in sorted(plans.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))):
        cat = cats.get(cid)
        if cat is None or cat.kind not in KINDS or not cat.in_budget:
            continue
        rest = eng.remainder(plan, facts.get((cid, m)) if m == first else None, scheduled.get((cid, m), ZERO))
        if rest <= 0:
            continue
        day = eng.month_end(m) if cat.kind == "income" else max(m, today)
        added.append(cal.Item(key=f"budget:{cid}:{m.isoformat()}", source="budget", title=f"Бюджет: {cat.name}",
                              planned_day=day, amount=rest if cat.kind == "income" else -rest, kind=cat.kind,
                              category_id=str(cid)))
    added.sort(key=lambda i: (i.day, i.title))
    out = []
    for sc in await scenario_settings(db, user_id):
        items = [replace(i, amount=sc.apply(i.amount)) for i in planned + added]
        fc = cal.forecast(snap.start, items, today, horizon, snap.threshold)
        income = sum((i.amount for i in items if i.amount > 0), ZERO)
        expense = sum((i.amount for i in items if i.amount < 0), ZERO)
        low = min((d for d in fc.days if d.balance is not None), key=lambda d: (d.balance, d.day), default=None)
        out.append({
            "key": sc.key, "label": sc.label, "income_pct": sc.income_pct, "expense_pct": sc.expense_pct,
            "income": income.quantize(CENT), "expense": expense.quantize(CENT),
            "end_balance": fc.end_balance, "min_balance": fc.min_balance, "min_day": low.day if low else None,
            "first_below_zero": fc.first_below_zero, "first_below_threshold": fc.first_below_threshold,
            "days": [{"day": d.day, "balance": d.balance} for d in fc.days],
        })
    return {
        "today": today, "date_to": horizon, "months": months, "start": snap.start, "unknown": snap.unknown,
        "threshold": snap.threshold, "scenarios": out,
        "budget_added": [{"category": cats[uuid.UUID(i.category_id)].name, "day": i.day, "amount": i.amount}
                         for i in added],
    }


async def categories_for_template(db: AsyncSession, user_id: uuid.UUID) -> list[CashCategory]:
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    out = []
    for top, children in _tree([c for c in _budget_cats(cats, set()) if c.archived_at is None]):
        out += [top, *children]
    return out

