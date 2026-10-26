"""Обзор «Планирования» и ДДС (шаг 3.10).

Обзор — отдельный дашборд раздела (не главная): деньги сейчас по счетам, ближайшая выплата Ozon, ближайший кассовый
разрыв или «денег хватит до …», что ожидается за горизонт; ряды по дням: слева от сегодня — факт (операции и остаток по
ним), с сегодня — план (платежи календаря 3.9 и прогноз остатка). ДДС — `engine/cash_flow` по операциям-фактам.

Нет данных — «нет данных», а не 0: остаток счёта без начального остатка неизвестен, выплат Ozon без ключа нет.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.engine import cash_flow as cf
from app.engine import payment_calendar as cal
from app.models.ozon_balance import OzonBalanceDay
from app.models.planning import MoneyOperation, PlannedPayment
from app.services import plan_calendar
from app.services import planning as planning_svc
from app.services.plan_calendar import _d, _money, line_out
from app.services.sync.finance import today_msk

ZERO = Decimal(0)
PAST_DAYS = 14  # слева от «сегодня» — факт за две недели
UPCOMING_DAYS = 14  # ближайшие платежи и поступления


# ---------- остатки на дату ----------


async def _sum_ops(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID, lo: date, hi: date) -> Decimal:
    if lo > hi:
        return ZERO
    total = await db.scalar(planning_svc._current(
        select(func.coalesce(func.sum(MoneyOperation.amount), 0)).where(
            MoneyOperation.user_id == user_id, MoneyOperation.account_id == account_id,
            MoneyOperation.day >= lo, MoneyOperation.day <= hi,
        )
    ))
    return Decimal(total)


async def opening_at(db: AsyncSession, user_id: uuid.UUID, day: date,
                     account_id: uuid.UUID | None = None) -> tuple[Decimal | None, list[str]]:
    """Остаток всех счетов (или одного — account_id) на начало дня day и счета без начального остатка (их остаток
    неизвестен).

    Начальный остаток счёта — на начало дня `as_of`: раньше него остаток = начальный − операции между днём и `as_of`,
    позже — начальный + операции с `as_of` до дня. Ни у одного счёта остатка нет — None."""
    openings = await planning_svc._openings(db, user_id)
    total, known, unknown = ZERO, False, []
    for acc in await planning_svc._accounts(db, user_id):
        if account_id is not None and acc.id != account_id:
            continue
        o = openings.get(acc.id)
        if o is None:
            unknown.append(acc.name)
            continue
        if o.as_of <= day:
            value = Decimal(o.amount) + await _sum_ops(db, user_id, acc.id, o.as_of, day - timedelta(days=1))
        else:
            value = Decimal(o.amount) - await _sum_ops(db, user_id, acc.id, day, o.as_of - timedelta(days=1))
        total, known = total + value, True
    return (total.quantize(Decimal("0.01")) if known else None), unknown


async def _flows(db: AsyncSession, user_id: uuid.UUID, lo: date, hi: date) -> tuple[list[MoneyOperation], dict]:
    rows = list((await db.execute(planning_svc._current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.day >= lo, MoneyOperation.day <= hi,
    )).order_by(MoneyOperation.day))).scalars())
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    return rows, cats


# ---------- ДДС ----------


TRANSFER_KEY = "transfer"  # по одному счёту перевод — приход или расход этого счёта
PLAN_MAX_DAYS = 400  # как у календаря: длиннее — только факт


async def cash_flow(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, granularity: str,
                    account_id: uuid.UUID | None = None) -> dict:
    """ДДС: факт по операциям и (шаг 10.24) план календаря — оставшиеся платежи с сегодня дают прогнозный остаток на
    конец периодов, план периода — план-факт по статьям. account_id — один счёт: его остаток, его операции и платежи;
    переводы со своими счетами тогда — приход и расход этого счёта."""
    today = today_msk()
    rows, cats = await _flows(db, user_id, date_from, date_to)
    if account_id is not None:
        rows = [r for r in rows if r.account_id == account_id]
    flows = []
    for r in rows:
        amount = Decimal(r.amount)
        if r.transfer_id is not None and account_id is not None:
            flows.append(cf.Flow(r.day, amount, TRANSFER_KEY, "income" if amount > 0 else "expense"))
            continue
        flows.append(cf.Flow(r.day, amount, str(r.category_id) if r.category_id else None,
                             cats[r.category_id].kind if r.category_id in cats else None,
                             transfer=r.transfer_id is not None))
    opening, unknown = await opening_at(db, user_id, date_from, account_id)
    accounts = {a.id: a.name for a in await planning_svc._accounts(db, user_id)}
    openings = await planning_svc._openings(db, user_id)
    blind = sorted({accounts[r.account_id] for r in rows if r.account_id not in openings and r.transfer_id is None})
    warnings = []
    if blind:
        opening = None  # по счёту без остатка есть движение — остатки по периодам были бы неверны
        warnings.append(
            f"Остаток не указан у счёта: {', '.join(blind)}, а движение по нему есть — остатки на начало и конец "
            "периодов не считаем. Укажите остаток в «Операциях» → «Счета и выписки»."
        )
    elif unknown and opening is not None:
        warnings.append(f"Остаток не указан: {', '.join(unknown)} — остатки без этих счетов.")

    # план: платежи календаря 3.9 — оставшиеся с сегодня (прогноз) и все с датой в периоде (план-факт)
    forecast: list[cf.Flow] = []
    planned: list[cf.Flow] = []
    plan_note = None
    causes = None
    settings = await planning_svc.get_settings(db, user_id)
    if settings is None or not settings.enabled:
        plan_note = "План появится, когда включено «Планирование»"
    elif (date_to - date_from).days + 1 > PLAN_MAX_DAYS:
        plan_note = f"План и прогноз — при периоде до {PLAN_MAX_DAYS} дней"
    else:
        snap = await plan_calendar.snapshot(db, user_id, date_from, max(date_to, today))
        by_code = {c.code: c.id for c in cats.values() if c.code and c.archived_at is None}
        for item in snap.items:
            acc = item.meta.get("account_id") or (settings.ozon_account_id if item.source == "ozon" else None)
            if account_id is not None and acc != account_id:
                continue
            cid = item.category_id or item.meta.get("category_id") or by_code.get(plan_calendar.TAX_CATEGORY.get(item.source))
            key = str(cid) if cid else None
            flow = cal.forecast_flow(item, today)
            if flow is not None and date_from <= flow[0] <= date_to:
                forecast.append(cf.Flow(flow[0], flow[1], key, item.kind))
            if (date_from <= item.day <= date_to and item.amount is not None
                    and item.status(today) not in ("skipped", "disabled")):
                planned.append(cf.Flow(item.day, item.amount, key, item.kind))
        if account_id is None and date_to >= today:
            causes = plan_calendar.causes_out(snap)

    parents = {str(c.id): str(c.parent_id) for c in cats.values() if c.parent_id}
    result = cf.cash_flow(opening, flows, date_from, date_to, granularity, parents, forecast=forecast, planned=planned)
    if result.transfers:
        warnings.append(f"Переводов между своими счетами: {result.transfers} — они не приход и не расход, в ДДС не входят.")

    def name(key: str | None, section: str) -> str:
        if key is None:
            return "Без статьи"
        if key == TRANSFER_KEY:
            return "Переводы между своими счетами"
        cat = cats.get(uuid.UUID(key))
        return cat.name if cat else "Статья удалена"

    def row_out(r: cf.Row, children: bool = True) -> dict:
        return {
            "category_id": None if r.category in (None, TRANSFER_KEY) else r.category,
            "transfer": r.category == TRANSFER_KEY, "name": name(r.category, r.section), "section": r.section,
            "values": [_money(v) for v in r.values], "total": _money(r.total),
            "forecast": [_money(v) for v in r.forecast], "forecast_total": _money(r.forecast_total),
            "plan": [_money(v) for v in r.plan], "plan_total": _money(r.plan_total),
            "children": [row_out(c, False) for c in r.children] if children else [],
        }

    def kind(p: cf.Period) -> str:
        return "past" if p.end < today else "future" if p.start > today else "current"

    return {
        "date_from": _d(date_from), "date_to": _d(date_to), "granularity": granularity, "today": _d(today),
        "account_id": account_id,
        "opening": _money(result.opening), "closing": _money(result.closing),
        "income": _money(result.income), "expense": _money(result.expense), "net": _money(result.net),
        "forecast_income": _money(result.forecast_income), "forecast_expense": _money(result.forecast_expense),
        "plan_income": _money(result.plan_income), "plan_expense": _money(result.plan_expense),
        "periods": [
            {"date_from": _d(p.start), "date_to": _d(p.end), "kind": kind(p), "opening": _money(p.opening),
             "income": _money(p.income), "expense": _money(p.expense), "net": _money(p.net),
             "forecast_income": _money(p.forecast_income), "forecast_expense": _money(p.forecast_expense),
             "plan_income": _money(p.plan_income), "plan_expense": _money(p.plan_expense),
             "closing": _money(p.closing)}
            for p in result.periods
        ],
        "rows": [row_out(r) for r in result.rows],
        "has_data": bool(result.rows),
        "has_plan": bool(forecast or planned),
        "plan_note": plan_note,
        "causes": causes,
        "warnings": warnings,
    }


# ---------- обзор ----------


def _kind_of(item: cal.Item) -> str:
    if item.source == "ozon":
        return "ozon"
    return "income" if item.kind == "income" else "expense"


async def _series(
    db: AsyncSession, user_id: uuid.UUID, snap: plan_calendar.Snapshot, date_from: date, date_to: date,
) -> list[dict]:
    """Ряд по дням окна: до сегодня — факт (операции и остаток по ним), с сегодня — план (платежи календаря) и прогноз
    остатка. Окно может быть целиком в прошлом (только факт) или в будущем (только план)."""
    today = snap.ctx.today
    # факт до сегодня: операции и остаток по ним (переводы не меняют общий остаток); сегодня — что уже пришло и ушло
    fact: dict[date, dict[str, Decimal]] = {}
    opening = None
    if date_from <= today:
        rows, _ = await _flows(db, user_id, date_from, min(date_to, today))
        opening, _ = await opening_at(db, user_id, date_from)
        for r in rows:
            if r.transfer_id is not None:
                continue
            key = "ozon" if r.source == "ozon_payout" else ("income" if r.amount > 0 else "expense")
            bucket = fact.setdefault(r.day, {"ozon": ZERO, "income": ZERO, "expense": ZERO})
            bucket[key] += Decimal(r.amount)
    plan: dict[date, dict[str, Decimal]] = {}
    for item in snap.items:  # «ожидается» со сроком в прошлом — в прогнозе сегодня (шаг 10.24)
        flow = cal.forecast_flow(item, today)
        if flow is not None and max(today, date_from) <= flow[0] <= date_to:
            bucket = plan.setdefault(flow[0], {"ozon": ZERO, "income": ZERO, "expense": ZERO})
            bucket[_kind_of(item)] += flow[1]
    forecast = {d.day: d.balance for d in snap.forecast.days} if snap.forecast else {}

    series = []
    balance = opening
    for n in range((date_to - date_from).days + 1):
        d = date_from + timedelta(days=n)
        past = d < today
        f = fact.get(d, {})
        p = plan.get(d, {}) if not past else {}
        if past:
            if balance is not None:
                balance = balance + sum(f.values(), ZERO)
            day_balance = balance
        else:
            day_balance = forecast.get(d)
        flows = {k: f.get(k, ZERO) + p.get(k, ZERO) for k in ("ozon", "income", "expense")}
        series.append({
            "day": _d(d), "kind": "fact" if past else "plan", "balance": _money(day_balance),
            "ozon": _money(flows["ozon"]), "income": _money(flows["income"]), "expense": _money(flows["expense"]),
            "fact": {k: _money(v) for k, v in f.items()} if f and not past else None,  # сегодня: уже пришло и ушло
        })
    return series


async def series(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """Ряд для графика за любой период: прошлое, будущее или всё вместе. Платежи календаря и прогноз — как в обзоре
    (календарь от двух недель назад), чтобы остаток в одни и те же дни совпадал."""
    today = today_msk()
    snap = await plan_calendar.snapshot(db, user_id, today - timedelta(days=PAST_DAYS), max(date_to, today))
    return {
        "today": _d(today), "date_from": _d(date_from), "date_to": _d(date_to), "threshold": _money(snap.threshold),
        "series": await _series(db, user_id, snap, date_from, date_to),
    }


async def overview(db: AsyncSession, user_id: uuid.UUID, horizon_days: int) -> dict:
    today = today_msk()
    date_from, date_to = today - timedelta(days=PAST_DAYS), today + timedelta(days=horizon_days - 1)
    snap = await plan_calendar.snapshot(db, user_id, date_from, date_to)
    ctx = snap.ctx
    series = await _series(db, user_id, snap, date_from, date_to)

    ahead = [(i, f) for i in snap.items if (f := cal.forecast_flow(i, today)) is not None and f[0] <= date_to]
    expected = {k: sum((f[1] for i, f in ahead if _kind_of(i) == k), ZERO) for k in ("ozon", "income", "expense")}
    unknown_amount = [i for i in snap.visible if i.amount is None and i.status(today) == "planned" and i.day >= today]
    next_ozon = next((i for i, _ in sorted(ahead, key=lambda x: (x[1][0], x[0].key)) if i.source == "ozon"), None)
    upcoming_to = today + timedelta(days=UPCOMING_DAYS - 1)
    upcoming = [i for i in snap.visible if today <= i.day <= upcoming_to and i.status(today) not in ("skipped", "disabled")]
    upcoming.sort(key=lambda i: (i.day, i.title))
    # к подтверждению (шаг 10.24): просрочены, оплачены частично, «ожидается» со сроком сегодня и раньше
    order = {"overdue": 0, "partial": 1, "expected": 2}
    to_confirm = [i for i in snap.visible if i.day <= today and i.status(today) in order]
    to_confirm.sort(key=lambda i: (order[i.status(today)], i.day, i.title))
    past = [i for i in snap.visible if date_from <= i.day <= today and i.status(today) in ("paid", "partial", "expected", "overdue")]
    plan_fact = {
        "date_from": date_from, **{k: sum(1 for i in past if i.status(today) == k) for k in ("paid", "partial", "expected", "overdue")},
        "plan": _money(sum((i.amount for i in past if i.amount is not None), ZERO)),
        "fact": _money(sum((i.fact.amount for i in past if i.fact is not None), ZERO)),
    }
    names = await plan_calendar._names(ctx)
    ozon_day = (await db.execute(select(OzonBalanceDay).where(OzonBalanceDay.user_id == user_id)
                                 .order_by(OzonBalanceDay.day.desc(), OzonBalanceDay.synced_at.desc()).limit(1))).scalar()

    accounts = await planning_svc.accounts(db, user_id)
    has_key = await crud_credential.has_active(db, user_id)
    has_ops = bool(await db.scalar(select(func.count()).select_from(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.deleted_at.is_(None), MoneyOperation.superseded_at.is_(None),
        MoneyOperation.source != "ozon_payout",
    )))
    has_plans = bool(await db.scalar(select(func.count()).select_from(PlannedPayment).where(
        PlannedPayment.user_id == user_id, PlannedPayment.deleted_at.is_(None), PlannedPayment.superseded_at.is_(None),
    )))
    groups = planning_svc.suggested_groups(ctx.supplier_payment)
    have = {c.code for c in await planning_svc._categories(db, user_id) if c.code and c.archived_at is None}
    suggestions = [] if has_plans else [
        {"code": c["code"], "name": c["name"], "why": c["why"], "have": c["code"] in have}
        for c in planning_svc.template_categories() if c.get("regular") and c["group"] in groups
    ]
    fc = snap.forecast
    return {
        "today": _d(today), "date_from": _d(date_from), "date_to": _d(date_to), "horizon_days": horizon_days,
        "balance": _money(snap.start), "unknown_accounts": snap.unknown,
        "accounts": [
            {"id": str(a["id"]), "name": a["name"], "balance": _money(a["balance"])}
            for a in accounts if not a["archived"]
        ],
        "threshold": _money(snap.threshold),
        "next_ozon": None if next_ozon is None else {
            "day": _d(next_ozon.day), "amount": _money(next_ozon.amount), "estimate": next_ozon.estimate,
            "title": next_ozon.title,
        },
        "ozon": {"enabled": ctx.gens["ozon"], "has_key": has_key},
        "first_below_zero": _d(fc.first_below_zero) if fc else None,
        "first_below_threshold": _d(fc.first_below_threshold) if fc else None,
        "min_balance": _money(fc.min_balance) if fc else None,
        "end_balance": _money(fc.end_balance) if fc else None,
        "gaps": [] if fc is None else [
            {"date_from": _d(g.date_from), "date_to": _d(g.date_to), "min_balance": _money(g.min_balance),
             "min_day": _d(g.min_day), "below_zero": g.below_zero}
            for g in fc.gaps
        ],
        "expected": {
            "ozon": _money(expected["ozon"]), "income": _money(expected["income"]),
            "expense": _money(expected["expense"]), "unknown": len(unknown_amount),
        },
        "series": series,
        "upcoming": [line_out(i, today, names) for i in upcoming],
        "to_confirm": [line_out(i, today, names) for i in to_confirm],
        "ozon_balance": None if ozon_day is None else {"amount": _money(ozon_day.closing), "day": ozon_day.day},
        "plan_fact": plan_fact,
        "causes": plan_calendar.causes_out(snap),
        "empty": {"has_ops": has_ops, "has_plans": has_plans, "suggestions": suggestions},
        "warnings": snap.notes,
    }
