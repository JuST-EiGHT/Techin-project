"""Платёжный календарь (шаг 3.9): откуда берётся план, план-факт, прогноз остатка, сроки налогов.

Платежи календаря (источник строки):
- manual — плановые платежи пользователя (`planned_payments`); закупка с отсрочкой — supplier;
- ozon — прогноз выплат Ozon (шаг 3.6): даты по графику, суммы по начислениям; вручную не вводятся;
- supplier — «поставщик получает деньги после продажи»: себестоимость проданного с НДС за неделю или месяц;
- tax — налоги, НДС и взносы: сроки — engine/tax_deadlines, суммы — по налоговому профилю за период.
Генераторы — производные: их платежи не хранятся и пересчитываются по данным (сменился профиль — будущие налоговые
платежи другие). Храним только переопределения отдельных платежей: перенос, своя сумма, пропуск, «оплачено».
Прошлые налоговые платежи при смене профиля задним числом — как решил пользователь (models.tax_profile.TaxPlanFreeze).
"""
import calendar as month_calendar
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payment_calendar as cal
from app.engine.payouts import monday
from app.engine.pnl import PnLEngine, PnLParams
from app.engine.tax import compute_tax, report_tax_input
from app.engine.tax_deadlines import Obligation, Segment, obligations
from app.models.planning import MoneyOperation, PlannedPayment, PlanningSettings
from app.models.tax_profile import TaxPlanFreeze, TaxProfile
from app.services import ozon_payouts, pnl_data
from app.services import payment_plan as pp
from app.services import planning as planning_svc
from app.services import user_settings as settings_svc
from app.services.costs import load_cost_history
from app.services.planning import NotFound, PlanningError
from app.services.sync.finance import today_msk
from app.services.tax_profile import load_profiles, supplier_vat_multiplier

ZERO = Decimal(0)
CENT = Decimal("0.01")
ENGINE = PnLEngine()
FIRST_DAY = date(2000, 1, 1)  # первый налоговый профиль действует «с начала учёта»
AVG_DAYS = 28  # продажи вперёд — по средним за последние 4 недели
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь",
          "декабрь")
QUARTERS = ("I", "II", "III", "IV")
TAX_CATEGORY = {"tax": "taxes", "supplier": "supplier", "ozon": "marketplace"}
NO_DATA = "Нет данных о продажах за период — загрузите отчёт по начислениям или подключите Ozon API"


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


def _days(a: date, b: date) -> list[date]:
    return [a + timedelta(days=n) for n in range((b - a).days + 1)]


# ---------- контекст ----------


@dataclass
class Figures:
    data: object  # NormalizedReportData периода (по дням с данными)
    owed: Decimal  # себестоимость проданного с НДС
    daily_owed: dict[date, Decimal]
    data_days: set[date]


@dataclass
class Ctx:
    db: AsyncSession
    user_id: uuid.UUID
    today: date
    settings: PlanningSettings | None
    supplier_payment: str
    deferral_days: int | None
    profiles: list[TaxProfile]
    freezes: list[TaxPlanFreeze]
    costs: object = None
    figures: dict = field(default_factory=dict)

    @property
    def gens(self) -> dict[str, bool]:
        s = self.settings
        return {"ozon": s.gen_ozon if s else True, "supplier": s.gen_supplier if s else True,
                "taxes": s.gen_taxes if s else True}

    @property
    def after_sale(self) -> bool:
        return self.supplier_payment in ("after_sale", "mixed")


async def context(db: AsyncSession, user_id: uuid.UUID) -> Ctx:
    us = await settings_svc.get_settings(db, user_id)
    freezes = (await db.execute(select(TaxPlanFreeze).where(TaxPlanFreeze.user_id == user_id))).scalars().all()
    return Ctx(
        db=db, user_id=user_id, today=today_msk(), settings=await db.get(PlanningSettings, user_id),
        supplier_payment=us.supplier_payment, deferral_days=us.supplier_deferral_days,
        profiles=await load_profiles(db, user_id), freezes=list(freezes),
    )


async def figures(ctx: Ctx, start: date, end: date, supplier_mult: Decimal) -> Figures | None:
    """Продажи периода по дням с данными (до вчера: сегодняшние начисления ещё не полные). Нет данных — None."""
    end = min(end, ctx.today - timedelta(days=1))
    if start > end:
        return None
    key = (start, end, supplier_mult)
    if key in ctx.figures:
        return ctx.figures[key]
    cov = await pnl_data.coverage(ctx.db, ctx.user_id, start, end, "auto")
    result = None
    if cov.data_source != "none":
        if ctx.costs is None:
            ctx.costs = await load_cost_history(ctx.db, ctx.user_id)
        lines = await pnl_data.load_lines(ctx.db, ctx.user_id, cov)
        period = pnl_data.build(lines, start, end, ctx.costs, supplier_mult)
        owed = ENGINE.owed(period.data, PnLParams(None, supplier_mult, period.article_vat))
        result = Figures(period.data, owed, period.cost_with_vat, {d for d, s in cov.days.items() if s})
    ctx.figures[key] = result
    return result


# ---------- свои плановые платежи ----------


def manual_items(ctx: Ctx, rows: list[PlannedPayment], lo: date, hi: date) -> list[cal.Item]:
    items = []
    for row in rows:
        sign = 1 if row.kind == "income" else -1
        for d in cal.occurrences(pp.rule(row, ctx.deferral_days), lo, hi):
            items.append(cal.Item(
                key=f"plan:{row.plan_id}:{d.isoformat()}", source="supplier" if row.purchase_day else "manual",
                title=row.title, planned_day=d, amount=sign * Decimal(row.amount), kind=row.kind, enabled=row.enabled,
                category_id=str(row.category_id) if row.category_id else None, counterparty=row.counterparty,
                meta={"plan_id": row.plan_id, "account_id": row.account_id, "category_id": row.category_id,
                      "comment": row.comment, "schedule": pp.schedule_text(row, ctx.deferral_days)},
            ))
    return items


# ---------- выплаты Ozon ----------


async def ozon_items(ctx: Ctx, lo: date, hi: date) -> tuple[list[cal.Item], list[str]]:
    if hi < ctx.today:
        return [], []
    weeks = min(26, (hi - ctx.today).days // 7 + 2)
    data = await ozon_payouts.payouts(ctx.db, ctx.user_id, weeks)
    if not data["has_data"]:
        return [], ["Выплат Ozon в прогнозе нет: нет данных о балансе Ozon — подключите ключ Ozon API."]
    items = []
    for f in data["forecast"]:
        d = date.fromisoformat(f["date"])
        if not lo <= d <= hi:
            continue
        week = ""
        if f["week_from"]:
            week = f" за {date.fromisoformat(f['week_from']):%d.%m}–{date.fromisoformat(f['week_to']):%d.%m}"
        estimate = f["kind"] == "estimate"
        items.append(cal.Item(
            key=f"ozon:{d.isoformat()}", source="ozon", title=f"Выплата Ozon{week}", planned_day=d,
            amount=Decimal(f["amount"]) if f["amount"] is not None else None, kind="income", estimate=estimate,
            counterparty="Ozon",
            meta={"note": "По начислениям следующих дней — оценка" if estimate else "По начислениям и графику выплат",
                  "link": "/dashboard/finance/payouts"},
        ))
    notes = [] if ctx.settings is None or ctx.settings.ozon_account_id else [
        "Выплаты Ozon не привязаны к счёту — выберите счёт в «Операциях» → «Счета и выписки», чтобы пришедшие выплаты попадали в остаток."
    ]
    return items, notes


# ---------- оплата поставщику после продажи ----------


def supplier_periods(period: str, day: int, lo: date, hi: date) -> list[tuple[date, date, date]]:
    """(начало, конец, дата оплаты) периодов продаж, оплата по которым — в окне [lo, hi]."""
    out = []
    if period == "month":
        y, m = lo.year, lo.month
        y, m = cal.add_months(y, m, -2)
        while date(y, m, 1) <= hi:
            start = date(y, m, 1)
            end = date(y, m, month_calendar.monthrange(y, m)[1])
            ny, nm = cal.add_months(y, m, 1)
            pay = date(ny, nm, min(day, month_calendar.monthrange(ny, nm)[1]))
            if lo <= pay <= hi:
                out.append((start, end, pay))
            y, m = ny, nm
        return out
    week = monday(lo) - timedelta(weeks=2)
    while week <= hi:
        pay = week + timedelta(days=7 + day - 1)
        if lo <= pay <= hi:
            out.append((week, week + timedelta(days=6), pay))
        week += timedelta(weeks=1)
    return out


async def supplier_items(ctx: Ctx, lo: date, hi: date) -> tuple[list[cal.Item], list[str]]:
    s = ctx.settings
    period, day = (s.supplier_period, s.supplier_day) if s else ("week", 1)
    periods = supplier_periods(period, day, lo, hi)
    if not periods:
        return [], []
    mult = await supplier_vat_multiplier(ctx.db, ctx.user_id)
    first = min(p[0] for p in periods)
    known_end = ctx.today - timedelta(days=1)
    fig = await figures(ctx, min(first, ctx.today - timedelta(days=AVG_DAYS)), known_end, mult)
    daily = fig.daily_owed if fig else {}
    data_days = fig.data_days if fig else set()
    recent = [d for d in _days(ctx.today - timedelta(days=AVG_DAYS), known_end) if d in data_days]
    avg = sum((daily.get(d, ZERO) for d in recent), ZERO) / len(recent) if recent else None
    items = []
    for start, end, pay in periods:
        total, estimate, unknown = ZERO, False, False
        for d in _days(start, end):
            if d <= known_end and d in data_days:
                total += daily.get(d, ZERO)
            elif avg is not None:
                total, estimate = total + avg, True
            else:
                unknown = True
        if not unknown and total == 0 and not estimate:
            continue  # продаж не было — платить нечего
        what = f"{start:%d.%m}–{end:%d.%m}" if period == "week" else f"{MONTHS[start.month - 1]} {start.year}"
        note = "Себестоимость проданного с НДС поставщика"
        if estimate:
            note += "; дни без данных и будущие — по средним продажам последних 4 недель"
        items.append(cal.Item(
            key=f"supplier:{start.isoformat()}", source="supplier", title=f"Оплата поставщику за продажи {what}",
            planned_day=pay, amount=None if unknown else -_q(total), kind="expense", estimate=estimate,
            meta={"note": NO_DATA if unknown else note, "period": (start, end),
                  "calc": (start, min(end, known_end)) if start <= known_end else None},
        ))
    return items, []


# ---------- налоги ----------


def _created(p: TaxProfile) -> datetime:
    c = p.created_at or datetime.min
    return c.replace(tzinfo=timezone.utc) if c.tzinfo is None else c


def segments(profiles: list[TaxProfile]) -> list[Segment]:
    """Отрезки действия профилей; для дат раньше первого — первый (как profile_on)."""
    by_from: dict[date, TaxProfile] = {}
    for p in sorted(profiles, key=lambda p: (p.effective_from, _created(p))):
        by_from[p.effective_from] = p
    ordered = sorted(by_from.items())
    out = []
    for i, (start, p) in enumerate(ordered):
        end = ordered[i + 1][0] - timedelta(days=1) if i + 1 < len(ordered) else None
        out.append(Segment(
            FIRST_DAY if i == 0 else start, end, p.regime, p.legal_form, p.vat_mode, p.has_employees,
            p.contributions_year is not None, ref=p,
        ))
    return out


def tax_obligations(ctx: Ctx, lo: date, hi: date, profiles: list[TaxProfile] | None = None) -> list[Obligation]:
    """Сроки в окне. Платёж со сроком до заморозки («оставить прошлые как были») — по профилям, записанным до неё."""
    profiles = ctx.profiles if profiles is None else profiles
    if not profiles:
        return []
    views: dict[datetime | None, list[Obligation]] = {None: obligations(segments(profiles), lo, hi)}
    for f in ctx.freezes:
        if f.profiles_before not in views:
            before = _created_cutoff(f.profiles_before)
            views[f.profiles_before] = obligations(segments([p for p in profiles if _created(p) < before]), lo, hi)

    def view_for(due: date) -> datetime | None:
        cuts = [f.profiles_before for f in ctx.freezes if due < f.due_before]
        return min(cuts, key=_created_cutoff) if cuts else None

    out = [o for view, obs in views.items() for o in obs if view_for(o.due) == view]
    return sorted(out, key=lambda o: (o.due, o.title, o.period_start))


def _created_cutoff(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def period_label(start: date, end: date) -> str:
    """«III кв. 2026», «октябрь 2026», «2026 год», «01.07–14.08.2026»."""
    last = month_calendar.monthrange(end.year, end.month)[1]
    if start.day == 1 and end.day == last and start.year == end.year:
        months = end.month - start.month + 1
        if months == 1:
            return f"{MONTHS[start.month - 1]} {start.year}"
        if months == 3 and start.month % 3 == 1:
            return f"{QUARTERS[start.month // 3]} кв. {start.year}"
        if months == 12:
            return f"{start.year} год"
        if months == 6 and start.month == 1:
            return f"I полугодие {start.year}"
    return f"{start:%d.%m}–{end:%d.%m.%Y}"


async def tax_amount(ctx: Ctx, ob: Obligation) -> tuple[Decimal | None, bool, str | None]:
    """Сумма платежа, оценка ли она, пояснение. Нет данных — None (не 0)."""
    p: TaxProfile = ob.segment.ref
    days = (ob.period_end - ob.period_start).days + 1
    year_days = 366 if month_calendar.isleap(ob.full_start.year) else 365
    if ob.what in ("patent", "contributions"):
        yearly = p.patent_cost_year if ob.what == "patent" else p.contributions_year
        if yearly is None:
            what = "стоимость патента" if ob.what == "patent" else "страховые взносы за год"
            return None, False, f"Сумма не указана — укажите {what} в налоговом профиле"
        part = "" if days >= year_days else f" за {days} дн. из {year_days}"
        return _q(Decimal(yearly) * days / year_days * ob.share), False, f"По налоговому профилю{part}"
    mult = 1 + Decimal(p.supplier_vat_pct) / 100
    fig = await figures(ctx, ob.period_start, ob.period_end, mult)
    if fig is None:
        return None, False, NO_DATA
    result = compute_tax(p.to_engine(), report_tax_input(fig.data, fig.owed, fig.data.auto_total), ob.period_start)
    known = len(fig.data_days)
    scale = Decimal(days) / known if known < days else Decimal(1)
    estimate = known < days
    note = f"Оценка: данные за {known} дн. из {days}, остальные дни — в том же темпе" if estimate else "По данным периода"
    if ob.what == "tax":
        value = result.tax * scale
    elif ob.what == "vat":
        value = result.vat * scale
    else:  # contributions_extra — 1 % с дохода свыше порога
        if result.base is None:
            return None, False, "На патенте 1 % считается от потенциального дохода — укажите сумму вручную"
        value = max(result.base * scale - Decimal(ob.extra["threshold"]), ZERO) * Decimal(ob.extra["rate"]) / 100
    return _q(value * ob.share), estimate, note


async def tax_item(ctx: Ctx, ob: Obligation) -> cal.Item:
    amount, estimate, note = await tax_amount(ctx, ob)
    partial = (ob.period_start, ob.period_end) != (ob.full_start, ob.full_end)
    if partial:
        note = f"Режим действовал часть периода: с {ob.period_start:%d.%m.%Y} по {ob.period_end:%d.%m.%Y}. {note or ''}".strip()
    calc = (ob.period_start, min(ob.period_end, ctx.today - timedelta(days=1)))
    return cal.Item(
        key=ob.key, source="tax", title=ob.title, planned_day=ob.due, amount=None if amount is None else -amount,
        kind="expense", estimate=estimate,
        meta={"note": note, "period": (ob.period_start, ob.period_end), "period_label": period_label(ob.full_start, ob.full_end),
              "notice": (ob.notice, ob.doc) if ob.notice else None, "due_rule": ob.due_rule,
              "calc": calc if ob.what in ("tax", "vat", "contributions_extra") and calc[0] <= calc[1] else None},
    )


# ---------- сбор календаря ----------


async def _ops(ctx: Ctx, lo: date, hi: date, extra_ids: set[uuid.UUID]) -> list[cal.Op]:
    q = planning_svc._current(select(MoneyOperation).where(
        MoneyOperation.user_id == ctx.user_id, MoneyOperation.transfer_id.is_(None),
        MoneyOperation.day >= lo, MoneyOperation.day <= hi,
    ))
    rows = {r.op_id: r for r in (await ctx.db.execute(q)).scalars()}
    missing = extra_ids - set(rows)
    if missing:
        q = planning_svc._current(select(MoneyOperation).where(
            MoneyOperation.user_id == ctx.user_id, MoneyOperation.op_id.in_(missing)))
        rows.update({r.op_id: r for r in (await ctx.db.execute(q)).scalars()})
    return [
        cal.Op(str(r.op_id), r.day, Decimal(r.amount), r.source, str(r.category_id) if r.category_id else None,
               r.counterparty)
        for r in rows.values()
    ]


async def build(ctx: Ctx, date_from: date, date_to: date) -> tuple[list[cal.Item], list[str], dict]:
    """Платежи с датой (после переноса) в окне — с запасом для сверки; переопределения применены, факт найден."""
    lo, hi = date_from - timedelta(days=pp.MAX_MOVE_DAYS), date_to + timedelta(days=pp.MAX_MOVE_DAYS)
    edge_lo, edge_hi = date_from - timedelta(days=cal.MATCH_DAYS), date_to + timedelta(days=cal.MATCH_DAYS)
    rows = await pp.plans(ctx.db, ctx.user_id)
    items = manual_items(ctx, rows, lo, hi)
    notes: list[str] = []
    gens = ctx.gens
    if gens["ozon"]:
        more, n = await ozon_items(ctx, lo, hi)
        items, notes = items + more, notes + n
    if gens["supplier"] and ctx.after_sale:
        more, n = await supplier_items(ctx, lo, hi)
        items, notes = items + more, notes + n
    overrides = {k: pp.to_engine(o) for k, o in (await pp.latest_overrides(ctx.db, ctx.user_id)).items()}
    cal.apply_overrides(items, overrides)
    if gens["taxes"]:
        for ob in tax_obligations(ctx, lo, hi):
            o = overrides.get(ob.key)
            day = o.day if o and o.day else ob.due
            if edge_lo <= day <= edge_hi:
                item = await tax_item(ctx, ob)
                cal.apply_overrides([item], overrides)
                items.append(item)
    items = [i for i in items if edge_lo <= i.day <= edge_hi]
    marked = {uuid.UUID(o.op_id) for o in overrides.values() if o.op_id}
    # с запасом назад: ЕНП платят заранее — платёж до срока закрывает плановый налог (шаг 3.12)
    ops = await _ops(ctx, edge_lo - timedelta(days=cal.TAX_EARLY_DAYS), min(edge_hi, ctx.today), marked)
    cal.match(items, ops, overrides)
    cal.close_taxes(items, ops, await _tax_categories(ctx))
    return items, notes, {"ops": ops}


async def _tax_categories(ctx: Ctx) -> set[str]:
    """Статья «Налоги и взносы» (код taxes): платёж с ней закрывает плановый налог (ЕНП)."""
    return {str(c.id) for c in await planning_svc._categories(ctx.db, ctx.user_id) if c.code == "taxes"}


async def _start_balance(ctx: Ctx) -> tuple[Decimal | None, list[str]]:
    balances = await planning_svc.balances(ctx.db, ctx.user_id)
    active = [a for a in await planning_svc._accounts(ctx.db, ctx.user_id) if a.archived_at is None]
    known = [balances[a.id] for a in active if balances.get(a.id) is not None]
    return (sum(known, ZERO) if known else None), [a.name for a in active if balances.get(a.id) is None]


def _d(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(_q(value))


def line_out(item: cal.Item, today: date, names: dict) -> dict:
    meta = item.meta
    period = meta.get("period")
    notice = meta.get("notice")
    calc = meta.get("calc")
    fact = item.fact
    return {
        "key": item.key, "source": item.source, "title": item.title, "kind": item.kind,
        "day": _d(item.day), "planned_day": _d(item.planned_day), "amount": _money(item.amount),
        "plan_amount": _money(item.plan_amount), "status": item.status(today), "estimate": item.estimate,
        "enabled": item.enabled, "moved": item.moved, "own_amount": item.own_amount, "skipped": item.skipped,
        "fact": None if fact is None else {
            "op_id": fact.op_id or None, "day": _d(fact.day), "amount": _money(fact.amount), "marked": item.fact_marked,
        },
        "diff": _money(item.diff) if item.diff not in (None, ZERO) and not meta.get("shared_fact") else None,
        "remaining": _money(item.remaining),  # частично оплачен — сколько осталось (со знаком плана)
        "shared_fact": bool(meta.get("shared_fact")),  # один платёж ЕНП закрыл несколько налогов с этим сроком
        "plan_id": str(meta["plan_id"]) if meta.get("plan_id") else None,
        "category": names["categories"].get(meta.get("category_id")),
        "account": names["accounts"].get(meta.get("account_id")),
        "counterparty": item.counterparty, "comment": meta.get("comment"), "schedule": meta.get("schedule"),
        "note": meta.get("note"), "link": meta.get("link"),
        "period": None if not period else {"date_from": _d(period[0]), "date_to": _d(period[1])},
        "period_label": meta.get("period_label"),
        "notice": None if not notice else {"day": _d(notice[0]), "doc": notice[1]},
        "calc": None if not calc else {"date_from": _d(calc[0]), "date_to": _d(calc[1])},
        "editable": item.source != "ozon",
    }


async def _names(ctx: Ctx) -> dict:
    accounts = {a.id: {"id": str(a.id), "name": a.name} for a in await planning_svc._accounts(ctx.db, ctx.user_id)}
    cats = {c.id: {"id": str(c.id), "name": c.name} for c in await planning_svc._categories(ctx.db, ctx.user_id)}
    return {"accounts": accounts, "categories": cats}


@dataclass
class Snapshot:
    """Календарь окна: платежи (с фактом), операции, остаток сейчас и прогноз — для календаря и обзора (3.10)."""

    ctx: Ctx
    items: list[cal.Item]  # с запасом по краям окна — для сверки
    visible: list[cal.Item]  # дата в окне
    notes: list[str]
    ops: list[cal.Op]
    start: Decimal | None  # остаток сейчас по счетам с указанным остатком
    unknown: list[str]  # счета без остатка
    threshold: Decimal | None
    forecast: cal.Forecast | None  # с сегодня; окно в прошлом — None


async def snapshot(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> Snapshot:
    ctx = await context(db, user_id)
    items, notes, extra = await build(ctx, date_from, date_to)
    visible = sorted([i for i in items if date_from <= i.day <= date_to], key=lambda i: (i.day, i.source, i.title))
    start, unknown = await _start_balance(ctx)
    raw = ctx.settings.threshold if ctx.settings else None
    threshold = Decimal(raw) if raw is not None else None
    fc = cal.forecast(start, items, ctx.today, date_to, threshold) if date_to >= ctx.today else None
    return Snapshot(ctx, items, visible, notes, extra["ops"], start, unknown, threshold, fc)


def causes_out(snap: "Snapshot") -> dict | None:
    """Кассовый разрыв (шаг 10.24): с какого дня, нехватка и почему — крупнейшие платежи до этого дня и поступления,
    которые не пришли. Разрыва ниже нуля нет — по порогу; ни того ни другого — None."""
    fc = snap.forecast
    if fc is None or not fc.gaps:
        return None
    gap = next((g for g in fc.gaps if g.below_zero), fc.gaps[0])
    floor = ZERO if gap.below_zero else (snap.threshold or ZERO)
    c = cal.gap_causes(snap.items, snap.ctx.today, gap.date_from)

    def ref(item: cal.Item, amount: Decimal | None) -> dict:
        return {"key": item.key, "title": item.title, "source": item.source, "day": _d(item.day),
                "amount": _money(amount)}

    return {
        "day": _d(gap.date_from), "below_zero": gap.below_zero, "min_balance": _money(gap.min_balance),
        "min_day": _d(gap.min_day), "shortfall": _money(floor - gap.min_balance),
        "expenses": [ref(i, amount) for i, amount in c.expenses],
        "missing_income": [ref(i, i.amount) for i in c.missing_income],
    }


async def calendar(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    snap = await snapshot(db, user_id, date_from, date_to)
    ctx, items, visible, notes, start, unknown, fc = (
        snap.ctx, snap.items, snap.visible, snap.notes, snap.start, snap.unknown, snap.forecast
    )
    today = ctx.today
    threshold = snap.threshold
    extra = {"ops": snap.ops}

    names = await _names(ctx)
    matched = cal.matched_ops(items)
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    facts = []
    fact_hi = min(date_to, today)
    for op in sorted(extra["ops"], key=lambda o: (o.day, o.op_id)):
        if op.op_id in matched or not date_from <= op.day <= fact_hi:
            continue
        cat = cats.get(uuid.UUID(op.category_id)) if op.category_id else None
        title = "Выплата Ozon" if op.source == "ozon_payout" else (op.counterparty or (cat.name if cat else "Операция"))
        facts.append({"op_id": op.op_id, "day": _d(op.day), "amount": _money(op.amount), "title": title,
                      "category": cat.name if cat else None, "source": op.source})

    overdue = [i for i in visible if i.status(today) == "overdue"]
    warnings = list(notes)
    if overdue:
        warnings.append(
            f"Платежей за прошлые дни без отметки «оплачено»: {len(overdue)}. В прогноз они не входят — отметьте "
            "оплату, перенесите на сегодня или позже или пропустите."
        )
    if unknown and start is not None:
        warnings.append(f"Остаток не указан: {', '.join(unknown)} — прогноз без этих счетов.")
    unknown_amount = [i for i in visible if i.amount is None and i.status(today) == "planned"]
    if unknown_amount:
        warnings.append(
            f"Сумма пока неизвестна у платежей: {len(unknown_amount)}. В прогноз они не входят — укажите сумму в карточке "
            "платежа («Своя сумма»)."
        )

    gens = ctx.gens
    has_profile = bool(ctx.profiles)
    hints = []
    if ctx.supplier_payment in ("own_production", "mixed"):
        hints = [{"code": c["code"], "name": c["name"], "why": c["why"]}
                 for c in planning_svc.template_categories() if c["group"] == "production"]
    return {
        "date_from": _d(date_from), "date_to": _d(date_to), "today": _d(today),
        "start_balance": _money(start), "unknown_accounts": unknown,
        "threshold": _money(threshold),
        "horizon_days": ctx.settings.horizon_days if ctx.settings else 60,
        "generators": {
            "ozon": {"enabled": gens["ozon"], "available": True},
            "supplier": {
                "enabled": gens["supplier"], "available": ctx.after_sale,
                "period": ctx.settings.supplier_period if ctx.settings else "week",
                "day": ctx.settings.supplier_day if ctx.settings else 1,
            },
            "taxes": {"enabled": gens["taxes"], "available": has_profile},
        },
        "supplier_payment": ctx.supplier_payment,
        "deferral_days": ctx.deferral_days if ctx.supplier_payment in ("deferred", "mixed") else None,
        "hints": hints,
        "lines": [line_out(i, today, names) for i in visible],
        "facts": facts,
        "days": [] if fc is None else [
            {"day": _d(d.day), "income": _money(d.income), "expense": _money(d.expense), "balance": _money(d.balance)}
            for d in fc.days
        ],
        "gaps": [] if fc is None else [
            {"date_from": _d(g.date_from), "date_to": _d(g.date_to), "min_balance": _money(g.min_balance),
             "min_day": _d(g.min_day), "below_zero": g.below_zero}
            for g in fc.gaps
        ],
        "first_below_zero": _d(fc.first_below_zero) if fc else None,
        "first_below_threshold": _d(fc.first_below_threshold) if fc else None,
        "min_balance": _money(fc.min_balance) if fc else None,
        "end_balance": _money(fc.end_balance) if fc else None,
        "overdue": len(overdue),
        "causes": causes_out(snap),
        "warnings": warnings,
    }


# ---------- один платёж: найти, отметить «оплачено» ----------


async def find_item(ctx: Ctx, key: str) -> cal.Item:
    source, anchor, plan_id = pp.parse_key(key)
    items: list[cal.Item] = []
    if plan_id is not None:
        row = await pp.current_plan(ctx.db, ctx.user_id, plan_id)
        items = manual_items(ctx, [row], anchor, anchor)
    elif source == "supplier":
        items, _ = await supplier_items(ctx, anchor, anchor + timedelta(days=70))
    elif source == "ozon":
        items, _ = await ozon_items(ctx, anchor, anchor)
    elif source == "tax":
        ob = next((o for o in tax_obligations(ctx, anchor, anchor + timedelta(days=600)) if o.key == key), None)
        items = [await tax_item(ctx, ob)] if ob else []
    item = next((i for i in items if i.key == key), None)
    if item is None:
        raise NotFound("Платёж не найден — обновите страницу")
    overrides = {k: pp.to_engine(o) for k, o in (await pp.latest_overrides(ctx.db, ctx.user_id)).items()}
    cal.apply_overrides([item], overrides)
    return item


async def _category_by_code(ctx: Ctx, code: str | None) -> uuid.UUID | None:
    if code is None:
        return None
    cats = await planning_svc._categories(ctx.db, ctx.user_id)
    return next((c.id for c in cats if c.code == code and c.archived_at is None), None)


async def mark_paid(
    db: AsyncSession, user_id: uuid.UUID, key: str, day: date | None = None, amount: Decimal | None = None,
    account_id: uuid.UUID | None = None, op_id: uuid.UUID | None = None,
) -> dict:
    """«Оплачено»: связать с операцией (op_id) или создать операцию-факт. Планирование не включено — отметка с датой
    и суммой. Коммит — на вызывающем. Возвращает {op_id, created}."""
    ctx = await context(db, user_id)
    item = await find_item(ctx, key)
    if item.source == "ozon":
        raise PlanningError("Выплата Ozon отмечается сама, когда приходит: оплаченной её делает выплата из данных Ozon")
    if op_id is not None:
        op = await planning_svc.current_operation(db, user_id, op_id)
        if op.transfer_id is not None:
            raise PlanningError("Перевод между своими счетами не может быть оплатой платежа")
        await pp.set_override(db, user_id, key, {"op_id": op.op_id, "paid_day": None, "paid_amount": None})
        return {"op_id": op.op_id, "created": False}
    value = amount if amount is not None else (abs(item.amount) if item.amount is not None else None)
    if value is None or value <= 0:
        raise PlanningError("Укажите сумму оплаты")
    value = planning_svc._check_amount(value)
    day = day or min(item.day, ctx.today)
    if day > ctx.today:
        raise PlanningError("Дата оплаты — не позже сегодняшнего дня")
    s = ctx.settings
    if s is None or s.setup_at is None:
        await pp.set_override(db, user_id, key, {"op_id": None, "paid_day": day, "paid_amount": value})
        return {"op_id": None, "created": False}
    accounts = [a for a in await planning_svc._accounts(db, user_id) if a.archived_at is None]
    account = account_id or item.meta.get("account_id") or s.ozon_account_id or (accounts[0].id if accounts else None)
    if account is None:
        raise PlanningError("Добавьте счёт в «Планировании → Счета» — оплата записывается на счёт")
    category = item.meta.get("category_id") or await _category_by_code(ctx, TAX_CATEGORY.get(item.source))
    period = item.meta.get("period_label")
    sign = 1 if item.kind == "income" else -1
    op = await planning_svc.create_operation(db, user_id, planning_svc.OperationIn(
        day=day, amount=sign * value, account_id=account, category_id=category, counterparty=item.counterparty,
        comment=f"{item.title}{f' · {period}' if period else ''} — по плану {item.day:%d.%m.%Y}",
    ))
    await db.flush()
    await pp.set_override(db, user_id, key, {"op_id": op.op_id, "paid_day": None, "paid_amount": None})
    return {"op_id": op.op_id, "created": True}


async def unmark_paid(db: AsyncSession, user_id: uuid.UUID, key: str) -> None:
    """Снять отметку «оплачено». Созданная операция остаётся — удалить её можно в «Операциях»."""
    await pp.set_override(db, user_id, key, {"op_id": None, "paid_day": None, "paid_amount": None})


async def move(db: AsyncSession, user_id: uuid.UUID, key: str, changes: dict) -> None:
    """Перенос, своя сумма, пропуск; reset — вернуть как по плану (отметка «оплачено» не меняется)."""
    ctx = await context(db, user_id)
    item = await find_item(ctx, key)
    if changes.pop("reset", False):
        changes = {"day": None, "amount": None, "skipped": False}
    await pp.set_override(db, user_id, key, changes, planned_day=item.planned_day)


# ---------- налоги: ближайшие сроки, прошлые платежи при смене профиля ----------


async def upcoming_taxes(db: AsyncSession, user_id: uuid.UUID, limit: int = 3) -> dict:
    """Ближайшие налоговые платежи (срок с сегодня): что, до какой даты, примерная сумма, оплачено ли."""
    ctx = await context(db, user_id)
    s = ctx.settings
    planning = bool(s and s.enabled and s.setup_at)
    if not ctx.profiles:
        return {"has_profile": False, "planning": planning, "items": []}
    overrides = {k: pp.to_engine(o) for k, o in (await pp.latest_overrides(db, user_id)).items()}
    picked: list[cal.Item] = []
    for ob in tax_obligations(ctx, ctx.today - timedelta(days=pp.MAX_MOVE_DAYS), ctx.today + timedelta(days=500)):
        o = overrides.get(ob.key)
        if (o.day if o and o.day else ob.due) < ctx.today or (o and o.skipped):
            continue
        item = await tax_item(ctx, ob)
        cal.apply_overrides([item], overrides)
        picked.append(item)
        if len(picked) >= limit:
            break
    picked.sort(key=lambda i: (i.day, i.title))
    marked = {uuid.UUID(o.op_id) for o in overrides.values() if o.op_id}
    ops = await _ops(ctx, ctx.today - timedelta(days=cal.TAX_EARLY_DAYS), ctx.today, marked)
    cal.match(picked, ops, overrides)
    cal.close_taxes(picked, ops, await _tax_categories(ctx))
    names = await _names(ctx)
    return {"has_profile": True, "planning": planning, "items": [line_out(i, ctx.today, names) for i in picked]}


async def past_changes(db: AsyncSession, user_id: uuid.UUID, candidate: TaxProfile) -> list[dict]:
    """Прошлые неоплаченные налоговые платежи, которые изменятся, если записать профиль candidate (ещё не в БД):
    срок до сегодня, за последний год. Пусто — спрашивать «пересчитать прошлые?» незачем."""
    ctx = await context(db, user_id)
    lo = max(candidate.effective_from, ctx.today - timedelta(days=366))
    hi = ctx.today - timedelta(days=1)
    if lo > hi:
        return []
    overrides = {k: pp.to_engine(o) for k, o in (await pp.latest_overrides(db, user_id)).items()}
    marked = {uuid.UUID(o.op_id) for o in overrides.values() if o.op_id}
    ops = await _ops(ctx, lo - timedelta(days=cal.MATCH_DAYS), ctx.today, marked)

    async def items_for(profiles: list[TaxProfile]) -> dict[str, cal.Item]:
        out = []
        for ob in tax_obligations(ctx, lo, hi, profiles):
            item = await tax_item(ctx, ob)
            cal.apply_overrides([item], overrides)
            out.append(item)
        cal.match(out, ops, overrides)
        return {i.key: i for i in out if i.fact is None and not i.skipped}

    old = await items_for(ctx.profiles)
    new = await items_for([*ctx.profiles, candidate])
    changes = []
    for key in sorted(set(old) | set(new), key=lambda k: ((old.get(k) or new.get(k)).day, k)):
        a, b = old.get(key), new.get(key)
        if a is not None and b is not None and a.amount == b.amount and a.title == b.title:
            continue
        ref = a or b
        changes.append({
            "key": key, "title": ref.title, "period_label": ref.meta.get("period_label"), "day": _d(ref.day),
            "old": _money(abs(a.amount)) if a and a.amount is not None else None,
            "new": _money(abs(b.amount)) if b and b.amount is not None else None,
            "removed": b is None, "added": a is None,
        })
    return changes


def freeze_past(user_id: uuid.UUID, record: TaxProfile, today: date) -> TaxPlanFreeze:
    """«Оставить прошлые платежи как были»: платежи со сроком до сегодня — по профилям до этой записи."""
    return TaxPlanFreeze(user_id=user_id, due_before=today, profiles_before=_created(record),
                         created_at=datetime.now(timezone.utc))
