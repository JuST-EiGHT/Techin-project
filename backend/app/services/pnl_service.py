"""P&L за любой период (шаг 3.2): источник, кэш, сохранённые итоги закрытых месяцев.

get_pnl(user, date_from, date_to, source, vat_multiplier, tax_rate, tax_base, manual_total):
- данные — строки начислений по дням (services/pnl_data): auto — XLSX, где есть отчёт, иначе API;
  в ответе всегда видно, какой источник за сколько дней использован и каких дней нет;
- расчёт — тот же движок, что у отчёта (engine/pnl, налог по профилю на начало периода);
- кэш 5 минут (pnl_cache); закрытый месяц без своих параметров — из pnl_snapshots, если снимок свежий.
Сброс при изменении данных — services/pnl_stale.invalidate (отчёты, себестоимость, налоговый профиль,
начисления): снимки, чей период задет, помечаются устаревшими, кэш пользователя сбрасывается.
"""
import calendar
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.pnl import PnLEngine, PnLParams
from app.engine.tax import Profile, pct
from app.models.accrual import SOURCE_API, SOURCE_XLSX, AccrualDay
from app.models.pnl_snapshot import PnLSnapshot
from app.models.report import Report
from app.services import pnl_data
from app.services.costs import load_cost_history
from app.services.pnl_cache import cache
from app.services.report_pnl import ReportPnL, to_out
from app.services.sync.finance import today_msk
from app.services.tax_profile import default_supplier_vat_pct, load_profiles, profile_on, profile_title

ENGINE = PnLEngine()
MAX_DAYS = 366


class PnLRequestError(Exception):
    """Параметры запроса не подходят. Текст — для пользователя."""


@dataclass(frozen=True)
class PnLRequest:
    date_from: date
    date_to: date
    source: str = "auto"  # auto | xlsx | api
    vat_multiplier: Decimal | None = None  # свой НДС поставщика для расчёта
    tax_rate: Decimal | None = None  # свой процент налога (доля) для расчёта
    tax_base: str | None = None  # profit | revenue — к tax_rate
    manual_total: Decimal | None = None  # «сколько реально пришло» за период

    def check(self) -> None:
        if self.date_from > self.date_to:
            raise PnLRequestError("Период: дата начала позже даты окончания")
        if (self.date_to - self.date_from).days + 1 > MAX_DAYS:
            raise PnLRequestError(f"Период: не больше {MAX_DAYS} дней за раз")
        if self.tax_base is not None and self.tax_rate is None:
            raise PnLRequestError("Налог считается: укажите и ставку налога (tax_rate)")

    @property
    def is_default(self) -> bool:
        return self.vat_multiplier is None and self.tax_rate is None and self.manual_total is None

    def params(self) -> dict:
        return {k: (str(v) if v is not None else None) for k, v in asdict(self).items()}


def is_closed_month(date_from: date, date_to: date) -> bool:
    last = calendar.monthrange(date_from.year, date_from.month)[1]
    return date_from.day == 1 and date_to == date_from.replace(day=last) and date_to < today_msk()


def _fmt(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _ranges_text(ranges: list[tuple[date, date]], limit: int = 4) -> str:
    parts = [_fmt(a) if a == b else f"{_fmt(a)}–{_fmt(b)}" for a, b in ranges[:limit]]
    more = f" и ещё {len(ranges) - limit}" if len(ranges) > limit else ""
    return ", ".join(parts) + more


async def compute(db: AsyncSession, user_id: uuid.UUID, req: PnLRequest) -> dict:
    cov = await pnl_data.coverage(db, user_id, req.date_from, req.date_to, req.source)
    lines = await pnl_data.load_lines(db, user_id, cov)
    costs = await load_cost_history(db, user_id)
    profiles = await load_profiles(db, user_id)
    row = profile_on(profiles, req.date_from)

    if req.vat_multiplier is not None:
        supplier = req.vat_multiplier
    else:
        supplier = 1 + (Decimal(row.supplier_vat_pct) if row else default_supplier_vat_pct(req.date_from)) / 100
    period = pnl_data.build(lines, req.date_from, req.date_to, costs, supplier)
    data = period.data

    profile, title = (row.to_engine(), profile_title(row)) if row else (None, None)
    if req.tax_rate is not None:
        rate = (req.tax_rate * 100).quantize(Decimal("0.01"))
        base = req.tax_base or "profit"
        profile = Profile("custom", row.legal_form if row else None, rate_pct=rate, custom_base=base)
        title = f"Свой процент: {pct(rate)} {'от дохода' if base == 'revenue' else 'от прибыли'} — параметр расчёта"

    warnings: list[str] = []
    delta, adjusted = pnl_data.manual_adjustment(cov)
    manual = req.manual_total
    if manual is None and adjusted:
        manual = data.auto_total + delta
        names = ", ".join(r.period for r in adjusted)
        warnings.append(f"Учтены ручные суммы «сколько пришло» из отчётов: {names}.")

    api_days, missing = cov.count(SOURCE_API), cov.missing
    if api_days:
        warnings.append(
            f"По данным Ozon API — {api_days} дн. из {len(cov.days)}: они могут расходиться с личным кабинетом "
            "(оговорка Ozon). Для точного P&L загрузите отчёт по начислениям за эти дни."
        )
    if missing and cov.data_source != "none":
        days = sum((b - a).days + 1 for a, b in missing)
        warnings.append(
            f"Нет данных за {days} дн.: {_ranges_text(missing)} — P&L неполный. Загрузите отчёт по начислениям "
            "или синхронизируйте начисления из Ozon API."
        )
    for r in cov.realization:
        warnings.append(
            f"Отчёт о реализации {_fmt(r.period_start)}–{_fmt(r.period_end)} по дням не делится и в P&L периода "
            "не входит — его остаток и маржа на странице отчёта."
        )
    changed = [p for p in profiles if req.date_from < p.effective_from <= req.date_to]
    if changed and req.tax_rate is None:
        warnings.append(
            f"Налоговый профиль менялся внутри периода (с {_fmt(changed[0].effective_from)}) — налог посчитан по "
            "профилю на начало периода. Для точного налога выберите период до или после этой даты."
        )

    has_data = cov.data_source != "none"
    pnl = None
    if has_data:
        params = PnLParams(
            tax_profile=profile, supplier_vat=supplier, article_vat=period.article_vat, manual_total=manual,
            day=req.date_from, data_source=cov.data_source,
        )
        result = ENGINE.compute(data, params)
        pnl = to_out(ReportPnL(result, title, supplier.quantize(Decimal("0.001")))).model_dump(mode="json")
    else:
        warnings.append(
            "За период нет данных — загрузите отчёт по начислениям из личного кабинета Ozon или подключите ключ "
            "Ozon API."
        )

    return {
        "date_from": req.date_from.isoformat(),
        "date_to": req.date_to.isoformat(),
        "source": req.source,
        "data_source": cov.data_source,
        "coverage": {
            "days_total": len(cov.days),
            "xlsx_days": cov.count(SOURCE_XLSX),
            "api_days": api_days,
            "missing_days": cov.count(None),
            "missing": [{"date_from": a.isoformat(), "date_to": b.isoformat()} for a, b in missing],
        },
        "reports": [
            {
                "id": str(r.id), "period": r.period, "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(), "manual_total_applied": r in adjusted,
            }
            for r in cov.reports
        ],
        "auto_total": str(data.auto_total) if has_data else None,
        "revenue_base": str(data.revenue_base) if has_data else None,
        "cost_sales": str(data.cost_sales.quantize(Decimal("0.01"))) if has_data else None,
        "by_type": {k: str(v) for k, v in data.by_type.items()},
        "missing_cost": [m.model_dump() for m in data.missing_cost],
        "pnl": pnl,
        "warnings": warnings + (pnl["warnings"] if pnl else []),
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "from_snapshot": False,
    }


async def get_pnl(db: AsyncSession, user_id: uuid.UUID, req: PnLRequest) -> dict:
    req.check()
    params = req.params()
    if (hit := await cache.get(user_id, "pnl", params)) is not None:
        return hit
    snapshot_ok = req.is_default and is_closed_month(req.date_from, req.date_to)
    snapshot = None
    if snapshot_ok:
        res = await db.execute(
            select(PnLSnapshot).where(
                PnLSnapshot.user_id == user_id, PnLSnapshot.date_from == req.date_from,
                PnLSnapshot.date_to == req.date_to, PnLSnapshot.source == req.source,
            )
        )
        snapshot = res.scalar_one_or_none()
        if snapshot is not None and not snapshot.stale:
            value = {**snapshot.data_json, "from_snapshot": True}
            await cache.set(user_id, "pnl", params, value)
            return value
    value = await compute(db, user_id, req)
    if snapshot_ok:
        now = datetime.now(timezone.utc)
        if snapshot is None:
            db.add(PnLSnapshot(
                user_id=user_id, date_from=req.date_from, date_to=req.date_to, source=req.source,
                data_json=value, calculated_at=now, stale=False,
            ))
        else:
            snapshot.data_json, snapshot.calculated_at = value, now
            snapshot.stale, snapshot.stale_reason, snapshot.stale_since = False, None, None
        await db.commit()
    await cache.set(user_id, "pnl", params, value)
    return value


async def get_by_day(db: AsyncSession, user_id: uuid.UUID, req: PnLRequest, report: Report | None = None) -> dict:
    """По дням: продажи, сколько начислено, себестоимость с НДС, прибыль до налога. Нет данных — null.
    report — дни одного отчёта (график на открытом отчёте): период и строки — его."""
    if report is not None:
        req = replace(req, date_from=report.period_start, date_to=report.period_end, source=SOURCE_XLSX)
    req.check()
    params = {**req.params(), "report_id": str(report.id) if report else None}
    if (hit := await cache.get(user_id, "by-day", params)) is not None:
        return hit
    if report is not None:
        cov = pnl_data.report_coverage(report)
    else:
        cov = await pnl_data.coverage(db, user_id, req.date_from, req.date_to, req.source)
    lines = await pnl_data.load_lines(db, user_id, cov)
    costs = await load_cost_history(db, user_id)
    row = profile_on(await load_profiles(db, user_id), req.date_from)
    if req.vat_multiplier is not None:
        supplier = req.vat_multiplier
    else:
        supplier = 1 + (Decimal(row.supplier_vat_pct) if row else default_supplier_vat_pct(req.date_from)) / 100
    period = pnl_data.build(lines, req.date_from, req.date_to, costs, supplier)
    totals: dict[date, Decimal] = {}
    revenue: dict[date, Decimal] = {}
    for ln in lines:
        totals[ln.day] = totals.get(ln.day, Decimal(0)) + ln.amount
        if pnl_data.is_revenue(ln):
            revenue[ln.day] = revenue.get(ln.day, Decimal(0)) + ln.amount
    days = []
    for day, source in cov.days.items():
        if source is None:
            days.append({"date": day.isoformat(), "source": None, "revenue": None, "total": None, "owed": None,
                         "profit_before_tax": None})
            continue
        total = totals.get(day, Decimal(0))
        owed = period.cost_with_vat.get(day, Decimal(0)).quantize(Decimal("0.01"))
        days.append({
            "date": day.isoformat(), "source": source, "revenue": str(revenue.get(day, Decimal(0))),
            "total": str(total), "owed": str(owed), "profit_before_tax": str(total - owed),
        })
    value = {
        "date_from": req.date_from.isoformat(), "date_to": req.date_to.isoformat(), "source": req.source,
        "data_source": cov.data_source, "days": days,
    }
    await cache.set(user_id, "by-day", params, value)
    return value


async def get_periods(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """За какие периоды есть данные: отчёты XLSX, загруженные из API дни, месяцы с покрытием и снимками."""
    res = await db.execute(
        select(Report)
        .where(Report.user_id == user_id, Report.deleted_at.is_(None))
        .order_by(Report.period_start.desc())
    )
    reports = [r for r in res.scalars() if r.period_start and r.period_end]
    res = await db.execute(select(AccrualDay.day).where(AccrualDay.user_id == user_id))
    api_days = set(res.scalars())
    xlsx_days = {
        d for r in reports if r.report_type == "accrual" for d in pnl_data.days_between(r.period_start, r.period_end)
    }
    known = xlsx_days | api_days | {d for r in reports for d in (r.period_start, r.period_end)}
    res = await db.execute(select(PnLSnapshot).where(PnLSnapshot.user_id == user_id, PnLSnapshot.source == "auto"))
    snapshots = {(s.date_from, s.date_to): s for s in res.scalars()}

    months = []
    if known:
        today = today_msk()
        year, month = min(known).year, min(known).month
        while (year, month) <= (today.year, today.month):
            start = date(year, month, 1)
            end = start.replace(day=calendar.monthrange(year, month)[1])
            days = pnl_data.days_between(start, end)
            xlsx = sum(d in xlsx_days for d in days)
            api = sum(d in api_days and d not in xlsx_days for d in days)
            snap = snapshots.get((start, end))
            months.append({
                "month": f"{year}-{month:02d}", "date_from": start.isoformat(), "date_to": end.isoformat(),
                "closed": end < today, "xlsx_days": xlsx, "api_days": api, "missing_days": len(days) - xlsx - api,
                "snapshot": {"calculated_at": snap.calculated_at.isoformat(), "stale": snap.stale} if snap else None,
            })
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return {
        "reports": [
            {"id": str(r.id), "report_type": r.report_type, "period": r.period,
             "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat()}
            for r in reports
        ],
        "api": [{"date_from": a.isoformat(), "date_to": b.isoformat()} for a, b in pnl_data.ranges(list(api_days))],
        "first_day": min(known).isoformat() if known else None,
        "last_day": max(known).isoformat() if known else None,
        "months": list(reversed(months)),  # новые сверху
    }
