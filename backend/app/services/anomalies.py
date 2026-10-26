"""Аномалии (шаг 7.1): сбор данных для детекторов, запуск, запись и чтение.

`detect` — раз в час (Celery, вид запуска anomalies в журнале sync_runs): собирает данные из готового (товары и маржа —
страница товаров 4.1, продажи — sales_daily 3.5, цены и комиссии — снимки 1.2, выплаты — 3.6, сверка с банком — 3.11,
календарь — 3.9, операции — 3.8; данные (10.5): окно «Синхронизация» 10.2, покрытие начислений 3.2, отчёты 2.2,
справочник себестоимости 2.3), прогоняет детекторы (engine/anomaly_detector) и сверяет с активными: новые
создаются, прошедшие закрываются (resolved_at), жёлтая, ставшая красной, — новой записью; у активных обновляются деньги
под риском, у сводных — и цифры. Проверки «Планирования» — только если оно включено; денежные проверки Ozon банка не
требуют. Сбой одной проверки не мешает остальным: её аномалии остаются как были. Записи не удаляются.

Порядок «Требует внимания» (10.5) — важность, затем деньги под риском, затем новые (`list_anomalies`, order=severity).
"""
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.engine import anomaly_detector as det
from app.engine import notifications as notify
from app.engine import payouts as payout_engine
from app.engine.anomaly_text import TYPE_TITLES, UNITS, describe
from app.models.anomaly import RED, YELLOW, Anomaly
from app.models.planning import MoneyOperation, PlanningSettings
from app.models.price_change import APPLIED, UNKNOWN, PriceChange
from app.models.product import Product, ProductPrice
from app.models.report import ERROR as REPORT_FAILED
from app.models.report import Report
from app.models.sales_daily import SalesDaily
from app.schemas.sync import utc
from app.services import bank, bank_connections, ozon_payouts, pnl_data, products, sales
from app.services.costs import sales_without_cost
from app.services.sync import overview, runner
from app.services.sync.finance import today_msk
from app.services.user_settings import anomaly_thresholds, get_settings

log = logging.getLogger(__name__)

SELLING_DAYS = 30  # «продаётся»: были продажи за 30 дней (или есть остаток) — для «маржи ниже порога»
BANK_DAYS = 30  # сверка выплат с банком — за 30 дней (и дальше — пока по старым есть активные аномалии)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Context:
    user_id: uuid.UUID
    now: datetime
    today: date
    th: det.Thresholds
    active: list[det.Active]  # активные на начало проверки (копии — сбой проверки откатывает сессию)
    planning: bool  # «Планирование» включено
    zero_stock: set[uuid.UUID]
    archived: set[uuid.UUID]

    def active_keys(self, type_: str) -> set[str]:
        return {a.key for a in self.active if a.type == type_}


# ── сбор данных для детекторов ──
async def _products(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Маржа, дни запаса и акции — как на странице товаров (шаг 4.1): одна и та же формула и пороги."""
    rows, pctx = await products.product_rows(db, ctx.user_id)
    selling_since = ctx.today - timedelta(days=SELLING_DAYS)
    margins, stocks, promos = [], [], []
    for r in rows:
        if r["is_archived"]:
            ctx.archived.add(r["id"])
            continue
        if r["stock"] == 0:
            ctx.zero_stock.add(r["id"])
        avg = r["avg_daily_sales"]
        if (r["stock"] or 0) > 0 or (r["last_sale"] is not None and r["last_sale"] >= selling_since):
            margins.append(det.MarginItem(r["id"], r["margin"], r["price"], avg, r["breakeven_price"]))
        if r["stock"] is not None:
            stocks.append(det.StockItem(r["id"], r["stock_status"], r["days_of_supply"], r["stock"], avg, r["cost_with_vat"]))
        promos += [det.PromoItem(r["id"], p.id, p.title, p.action_price, p.added_automatically, p.margin_in_promo, p.date_to,
                                 avg) for p in r["promotions"]]
    known = bool(pctx.sales)
    return [det.low_margin(margins, ctx.th), det.low_stock(stocks, ctx.th, sales_known=known),
            det.excess_stock(stocks, ctx.th, sales_known=known), det.promo_auto_added(promos),
            det.promo_loss(promos, ctx.today)]


async def _sales(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Продажи по дням за 31 день до вчера: магазин и товары в продаже. Дни без данных — не 0 (coverage)."""
    await sales.ensure_built(db, ctx.user_id)
    end = ctx.today - timedelta(days=1)
    start = ctx.today - timedelta(days=det.RECENT_DAYS + det.FACTOR_DAYS)
    cov = await pnl_data.coverage(db, ctx.user_id, start, end, "auto")
    covered = {d for d, source in cov.days.items() if source is not None}
    on_sale = {pid for pid, archived in await db.execute(
        select(Product.id, Product.is_archived).where(Product.user_id == ctx.user_id)) if not archived}
    shop: dict[date, int] = defaultdict(int)
    by_product: dict[uuid.UUID, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for day, pid, qty in await db.execute(
        select(SalesDaily.day, SalesDaily.product_id, SalesDaily.qty_sold)
        .where(SalesDaily.user_id == ctx.user_id, SalesDaily.day >= start, SalesDaily.day <= end)
    ):
        if day not in covered:
            continue
        shop[day] += qty
        if pid in on_sale:
            by_product[pid][day] += qty
    data = det.SalesData(ctx.today, covered, dict(shop), {k: dict(v) for k, v in by_product.items()}, ctx.zero_stock)
    return [det.sales_drop(data, ctx.th, ctx.active_keys(det.SALES_DROP)),
            det.sales_growth(data, ctx.th, ctx.active_keys(det.SALES_GROWTH))]


async def _prices(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Цена и комиссия: снимки за 7 дней и последний перед ними — по товарам в продаже, у которых за 7 дней были снимки
    (снимок пишется при изменении: нет снимков — ничего не менялось)."""
    start = ctx.now - det.CHANGE_WINDOW
    q = select(ProductPrice).join(Product, Product.id == ProductPrice.product_id).where(
        Product.user_id == ctx.user_id, Product.is_archived.is_(False))
    recent = list((await db.execute(q.where(ProductPrice.snapshot_at > start))).scalars())
    changed = {s.product_id for s in recent}
    before = (
        select(ProductPrice.product_id, func.max(ProductPrice.snapshot_at).label("mx"))
        .where(ProductPrice.product_id.in_(changed), ProductPrice.snapshot_at <= start)
        .group_by(ProductPrice.product_id).subquery()
    )
    earlier = list((await db.execute(select(ProductPrice).join(
        before, (ProductPrice.product_id == before.c.product_id) & (ProductPrice.snapshot_at == before.c.mx)
    ))).scalars()) if changed else []
    prices: dict[uuid.UUID, list[det.Point]] = defaultdict(list)
    commissions: dict[uuid.UUID, list[det.Point]] = defaultdict(list)
    for s in [*earlier, *recent]:
        at = utc(s.snapshot_at)
        prices[s.product_id].append(det.Point(at, s.price))
        commissions[s.product_id].append(det.Point(at, s.commission_pct))
    via_service: dict[uuid.UUID, list[datetime]] = defaultdict(list)
    for pid, at in await db.execute(select(PriceChange.product_id, PriceChange.created_at).where(
        PriceChange.user_id == ctx.user_id, PriceChange.status.in_((APPLIED, UNKNOWN)), PriceChange.created_at > start,
    )):
        via_service[pid].append(utc(at))
    return [det.price_change(prices, ctx.now, ctx.th, via_service), det.commission_change(commissions, ctx.now, ctx.th)]


async def _payouts(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Выплаты Ozon (шаг 3.6) — без банка: просрочка по графику и расхождение с прогнозом."""
    st = await ozon_payouts.load_state(db, ctx.user_id)
    s = st.schedule
    overdue = st.overdue()
    expected = {}
    for day in overdue:
        exp = payout_engine.expected(s, day, st.weeks)
        expected[day] = exp[1] if exp else None
    facts = []
    for p in st.facts:
        exp = payout_engine.expected(s, p.day, st.weeks)
        facts.append(det.PayoutFact(p.day, p.amount, exp[1] if exp else None, exp[0] if exp else None))
    # график, найденный по выплатам с низкой уверенностью, с выплатами расходится и так — не сверяем
    reliable = s.mode == "weekly" and s.lag_weeks is not None and not (s.source == "auto" and s.confidence == "low")
    return [det.payout_missed(overdue, {p.day for p in st.facts}, expected, ctx.active_keys(det.PAYOUT_MISSED)),
            det.payout_diff(facts, ctx.today, ctx.th, reliable)]


def _key_day(key: str) -> date:
    return date.fromisoformat(key.split(":", 1)[1])


async def _bank(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Выплаты Ozon ↔ поступления на счёт (шаг 3.11) — если включено «Планирование» и загружен банк."""
    rows: list[det.BankRow] = []
    has_bank = False
    if ctx.planning:
        keys = ctx.active_keys(det.BANK_MISSING) | ctx.active_keys(det.BANK_DIFF)
        start = min([ctx.today - timedelta(days=BANK_DAYS), *(_key_day(k) for k in keys)])
        check = await bank.ozon_check(db, ctx.user_id, start, ctx.today)
        has_bank = check["has_bank"]
        rows = [det.BankRow(r["payout_day"], r["status"], r["ozon_amount"], r["bank_amount"], r["bank_day"], r["diff"])
                for r in check["rows"] if r["payout_day"] is not None]
    enabled = ctx.planning and has_bank
    return [det.bank_missing(rows, enabled), det.bank_diff(rows, enabled)]


async def _cash_gap(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Прогноз кассового разрыва (шаг 3.9) на N дней вперёд — если включено «Планирование»."""
    if not ctx.planning:
        return [det.cash_gap([], ctx.today, ctx.th, None, enabled=False, start_known=False)]
    from app.services import plan_calendar  # тяжёлый модуль календаря — только когда планирование включено

    snap = await plan_calendar.snapshot(db, ctx.user_id, ctx.today, ctx.today + timedelta(days=ctx.th.cash_gap_days - 1))
    days = [det.CashDay(d.day, d.balance) for d in snap.forecast.days] if snap.forecast else []
    return [det.cash_gap(days, ctx.today, ctx.th, snap.threshold, enabled=True, start_known=snap.start is not None)]


async def _uncategorized(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Операции без статьи (шаг 3.8): сколько дней каждая без статьи — от её появления в сервисе (первая версия)."""
    if not ctx.planning:
        return [det.uncategorized([], ctx.now, ctx.th, enabled=False)]
    rows = list((await db.execute(select(MoneyOperation).where(
        MoneyOperation.user_id == ctx.user_id, MoneyOperation.superseded_at.is_(None), MoneyOperation.deleted_at.is_(None),
        MoneyOperation.category_id.is_(None), MoneyOperation.transfer_id.is_(None),
    ))).scalars())
    first: dict[uuid.UUID, datetime] = {}
    if rows:
        first = {op_id: utc(at) for op_id, at in await db.execute(
            select(MoneyOperation.op_id, func.min(MoneyOperation.created_at))
            .where(MoneyOperation.op_id.in_([r.op_id for r in rows])).group_by(MoneyOperation.op_id)
        )}
    ops = [det.OpItem(r.op_id, first.get(r.op_id, utc(r.created_at)), r.day, Decimal(r.amount)) for r in rows]
    return [det.uncategorized(ops, ctx.now, ctx.th, enabled=True)]


# ── данные (шаг 10.5) ──
REASONS = {problem: reason for reason, problem in overview.PROBLEMS.items()}
# само не пройдёт — исправить может только пользователь: ключ, права ключа, токен банка
ACTION_NEEDED = {runner.NO_KEY, runner.KEY, runner.KEY_ROLE, runner.BANK_TOKEN}


async def _sync(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Виды данных из окна «Синхронизация» (10.2): ошибка, давно не обновлялись, ещё не загружались. Ключа Ozon никогда не
    было (только отчёты) — видов Ozon нет, проверять нечего."""
    ov = await overview.build(db, ctx.user_id, ctx.now)
    keys = await crud_credential.list_for_user(db, ctx.user_id)
    banks = await bank_connections.list_connections(db, ctx.user_id)
    since = {True: utc(keys[0].created_at) if keys else None,
             False: utc(min(c.created_at for c in banks)) if banks else None}
    items = []
    for s in ov.states:
        reason = REASONS.get(s.problem) if s.problem else None
        needed = s.state == overview.ERROR and reason in ACTION_NEEDED
        # разовый сбой («повторим по расписанию») в список попадает, только когда затянулся: тогда совет — как у «давно»
        hint = None if s.problem is None else s.problem.hint if needed else overview.STALE_PROBLEM.hint
        items.append(det.SyncItem(
            s.kind, s.title, s.state, s.last_success_at, reason, s.problem.text if s.problem else None, hint,
            s.problem.action[1] if s.problem and s.problem.action else None, action_needed=needed,
            ozon=s.kind != overview.BANK, since=since[s.kind != overview.BANK],
        ))
    return [det.sync_problem(items, ctx.now, ctx.th, ctx.active_keys(det.SYNC_PROBLEM))]


async def _finance_data(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Последние 30 дней до вчера: дни без начислений между загруженными и продажи в дни без себестоимости (по
    справочнику на день продажи — как P&L). Дни без начислений — пропуски, продажи за них неизвестны."""
    end = ctx.today - timedelta(days=1)
    start = ctx.today - timedelta(days=det.DATA_DAYS)
    cov = await pnl_data.coverage(db, ctx.user_id, start, end, "auto")
    covered = {d for d, source in cov.days.items() if source is not None}
    await sales.ensure_built(db, ctx.user_id)
    items = await sales_without_cost(db, ctx.user_id, start, end, covered)
    return [det.accrual_gaps({d: d in covered for d in cov.days}, ctx.th),
            det.missing_cost(items, sales_known=bool(covered))]


async def _reports(db: AsyncSession, ctx: Context) -> list[det.Check]:
    """Отчёты XLSX, которые не удалось перечитать (не скрытые)."""
    rows = (await db.execute(select(Report).where(
        Report.user_id == ctx.user_id, Report.status == REPORT_FAILED, Report.deleted_at.is_(None)))).scalars()
    return [det.report_error([det.ReportItem(r.id, r.filename, r.period, r.error_message) for r in rows])]


# порядок важен: товары дают «нулевой остаток» и архив для продаж
GATHERERS = (("products", _products), ("sales", _sales), ("prices", _prices), ("payouts", _payouts),
             ("bank", _bank), ("cash_gap", _cash_gap), ("uncategorized", _uncategorized),
             ("sync", _sync), ("finance_data", _finance_data), ("reports", _reports))


async def active(db: AsyncSession, user_id: uuid.UUID) -> list[Anomaly]:
    return list((await db.execute(select(Anomaly).where(Anomaly.user_id == user_id, Anomaly.resolved_at.is_(None)))).scalars())


def _active(a: Anomaly) -> det.Active:
    return det.Active(a.id, a.type, a.key, a.severity, a.product_id)


async def detect(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    """Поиск аномалий пользователя: новые — записи, прошедшие — закрываются. Коммит — здесь. Возвращает статистику и id
    новых аномалий (для уведомлений — шаг 7.3)."""
    now = now or _now()
    settings = await get_settings(db, user_id)
    planning = await db.get(PlanningSettings, user_id)
    ctx = Context(user_id, now, today_msk(), anomaly_thresholds(settings),
                  [_active(a) for a in await active(db, user_id)], bool(planning and planning.enabled), set(), set())
    checks: list[det.Check] = []
    failed: list[str] = []
    for name, gather in GATHERERS:
        try:
            checks += await gather(db, ctx)
        except Exception:  # noqa: BLE001 — сбой одной проверки не мешает остальным; её аномалии остаются как были
            log.exception("Аномалии: проверка %s у пользователя %s упала", name, user_id)
            await db.rollback()
            failed.append(name)

    current = {a.id: a for a in await active(db, user_id)}  # после rollback прежние объекты устарели
    plan = det.reconcile([_active(a) for a in current.values()], checks, ctx.archived)
    for anomaly_id, resolution in plan.resolve.items():
        current[anomaly_id].resolved_at, current[anomaly_id].resolution = now, resolution
    for anomaly_id in plan.seen:
        current[anomaly_id].last_seen_at = now
    for anomaly_id, (f, live) in plan.refresh.items():
        a = current[anomaly_id]
        a.money_at_risk = f.at_risk  # оценка «сейчас»
        if live:  # сводный пункт: сколько товаров, дней, видов — как сейчас
            a.metric_before, a.metric_after, a.threshold, a.details = f.metric_before, f.metric_after, f.threshold, f.details or None
    await db.flush()  # закрытые — раньше новых: активная одного типа и предмета одна (уникальный индекс)
    created = [Anomaly(
        id=uuid.uuid4(), user_id=user_id, type=f.type, key=f.key, severity=f.severity, product_id=f.product_id,
        metric_before=f.metric_before, metric_after=f.metric_after, threshold=f.threshold, details=f.details or None,
        money_at_risk=f.at_risk, detected_at=now, last_seen_at=now,
    ) for f in plan.create]
    db.add_all(created)
    await db.commit()
    return {
        "created": len(created), "resolved": len(plan.resolve) - plan.escalated, "escalated": plan.escalated,
        "active": len(current) - len(plan.resolve) + len(created), "failed": failed,
        "new_ids": [str(a.id) for a in created],
    }


# ── чтение ──
STATUSES = ("active", "resolved", "all")


def item(a: Anomaly, product: Product | None, today: date, muted: set[str] | frozenset = frozenset()) -> dict:
    text = describe(a.type, a.severity, a.metric_before, a.metric_after, a.threshold, a.details, a.product_id,
                    product.name or product.offer_id if product else None, today)
    return {
        "id": a.id, "type": a.type, "type_title": TYPE_TITLES.get(a.type, a.type), "group": det.GROUPS.get(a.type),
        "section": notify.section_of(a.type), "muted": a.type in muted,
        "severity": a.severity, "title": text.title, "message": text.message, "link": text.link,
        "product": {"id": product.id, "offer_id": product.offer_id, "name": product.name} if product else None,
        "metric_before": a.metric_before, "metric_after": a.metric_after, "threshold": a.threshold,
        "unit": UNITS.get(a.type), "details": a.details or {}, "money_at_risk": a.money_at_risk,
        "detected_at": utc(a.detected_at), "last_seen_at": utc(a.last_seen_at), "resolved_at": utc(a.resolved_at),
        "resolution": a.resolution, "is_acknowledged": a.is_acknowledged, "acknowledged_at": utc(a.acknowledged_at),
    }


async def counts(db: AsyncSession, user_id: uuid.UUID, muted: set[str] | frozenset = frozenset()) -> dict:
    """Активные: всего и непрочитанные (без «Ок») — всего, красных и жёлтых; активные красные и жёлтые с «Ок» и без
    (`red_active`, `yellow_active` — числа у фильтров страницы). Для колокольчика (7.2) — один запрос. muted — типы
    выключенных разделов уведомлений (10.25): не считаются."""
    out = {"active": 0, "unread": 0, RED: 0, YELLOW: 0, "red_active": 0, "yellow_active": 0}
    q = select(Anomaly.severity, Anomaly.is_acknowledged, func.count()).where(
        Anomaly.user_id == user_id, Anomaly.resolved_at.is_(None))
    if muted:
        q = q.where(Anomaly.type.not_in(sorted(muted)))
    for severity, acknowledged, n in await db.execute(q.group_by(Anomaly.severity, Anomaly.is_acknowledged)):
        out["active"] += n
        out[f"{severity}_active"] = out.get(f"{severity}_active", 0) + n
        if not acknowledged:
            out["unread"] += n
            out[severity] = out.get(severity, 0) + n
    return out


def _filtered(user_id: uuid.UUID, *, status: str = "active", severity: str | None = None, group: str | None = None,
              type_: str | None = None, product_id: uuid.UUID | None = None, acknowledged: bool | None = None,
              area: str | None = None, section: str | None = None, muted: set[str] | frozenset = frozenset()):
    q = select(Anomaly).where(Anomaly.user_id == user_id)
    if section:
        q = q.where(Anomaly.type.in_(notify.types_of([section])))
    if muted:
        q = q.where(Anomaly.type.not_in(sorted(muted)))
    if area:
        q = q.where(Anomaly.type.in_(det.AREAS[area]))
    if status == "active":
        q = q.where(Anomaly.resolved_at.is_(None))
    elif status == "resolved":
        q = q.where(Anomaly.resolved_at.is_not(None))
    if severity:
        q = q.where(Anomaly.severity == severity)
    if group:
        q = q.where(Anomaly.type.in_([t for t, g in det.GROUPS.items() if g == group]))
    if type_:
        q = q.where(Anomaly.type == type_)
    if product_id:
        q = q.where(Anomaly.product_id == product_id)
    if acknowledged is not None:
        q = q.where(Anomaly.is_acknowledged.is_(acknowledged))
    return q


async def list_anomalies(
    db: AsyncSession, user_id: uuid.UUID, *, status: str = "active", severity: str | None = None,
    group: str | None = None, type_: str | None = None, product_id: uuid.UUID | None = None,
    acknowledged: bool | None = None, order: str | None = None, limit: int = 50, offset: int = 0,
    area: str | None = None, section: str | None = None, muted: set[str] | frozenset = frozenset(),
    hide_muted: bool = True,
) -> dict:
    """Аномалии по фильтрам. Порядок: severity — красные первыми, внутри — больше денег под риском, затем новые (по
    умолчанию у активных; «Требует внимания» 10.5); new — новые первыми (колокольчик, 7.2); у закрытых по умолчанию — по
    времени закрытия. by_group — сколько по группам при тех же
    фильтрах, кроме группы и типа (числа у фильтров страницы); by_section — так же по разделам уведомлений (10.25).
    muted — типы выключенных разделов: hide_muted — не показывать (колокольчик, «Требует внимания»), иначе — с пометкой
    (страница «Уведомления»)."""
    hidden = muted if hide_muted else frozenset()
    q = _filtered(user_id, status=status, severity=severity, group=group, type_=type_, product_id=product_id,
                  acknowledged=acknowledged, area=area, section=section, muted=hidden)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    order = order or ("severity" if status == "active" else "resolved")
    by = {
        "severity": (Anomaly.severity == YELLOW, Anomaly.money_at_risk.desc().nulls_last(), Anomaly.detected_at.desc()),
        "new": (Anomaly.detected_at.desc(),),
        "resolved": (func.coalesce(Anomaly.resolved_at, Anomaly.detected_at).desc(),),
    }[order]
    rows = list((await db.execute(q.order_by(*by, Anomaly.id).limit(limit).offset(offset))).scalars())
    names = {p.id: p for p in (await db.execute(select(Product).where(
        Product.id.in_({a.product_id for a in rows if a.product_id})))).scalars()} if rows else {}
    by_group: dict[str, int] = {}
    by_section: dict[str, int] = {}
    base = _filtered(user_id, status=status, severity=severity, product_id=product_id, acknowledged=acknowledged,
                     area=area, muted=hidden).subquery()
    for type_name, n in await db.execute(select(base.c.type, func.count()).group_by(base.c.type)):
        group_name = det.GROUPS.get(type_name)
        if group_name:
            by_group[group_name] = by_group.get(group_name, 0) + n
        section_name = notify.section_of(type_name)
        if section_name:
            by_section[section_name] = by_section.get(section_name, 0) + n
    today = today_msk()
    return {"items": [item(a, names.get(a.product_id), today, muted) for a in rows], "total": total, "limit": limit,
            "offset": offset, "by_group": by_group, "by_section": by_section}


async def acknowledge_all(
    db: AsyncSession, user_id: uuid.UUID, *, severity: str | None = None, group: str | None = None,
    type_: str | None = None, product_id: uuid.UUID | None = None, section: str | None = None,
    muted: set[str] | frozenset = frozenset(),
) -> int:
    """«Отметить все прочитанными» — активные без «Ок» (по тем же фильтрам, что на странице; выключенные разделы — не
    трогаем, их и не видно). Сколько отмечено."""
    rows = list((await db.execute(_filtered(
        user_id, status="active", severity=severity, group=group, type_=type_, product_id=product_id, acknowledged=False,
        section=section, muted=muted,
    ))).scalars())
    now = _now()
    for a in rows:
        a.is_acknowledged, a.acknowledged_at = True, now
    await db.commit()
    return len(rows)


class NotFound(Exception):
    pass


async def acknowledge(db: AsyncSession, user_id: uuid.UUID, anomaly_id: uuid.UUID,
                      muted: set[str] | frozenset = frozenset()) -> dict:
    """«Ок»: аномалия больше не просит внимания (остаётся активной, пока метрика не вернётся в норму). Повторно — без
    изменений."""
    a = await db.get(Anomaly, anomaly_id)
    if a is None or a.user_id != user_id:
        raise NotFound
    if not a.is_acknowledged:
        a.is_acknowledged, a.acknowledged_at = True, _now()
        await db.commit()
    product = await db.get(Product, a.product_id) if a.product_id else None
    return item(a, product, today_msk(), muted)
