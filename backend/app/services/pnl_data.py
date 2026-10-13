"""Данные P&L за любой период (шаг 3.2): строки начислений по дням из отчётов XLSX и из API.

Источник выбирается по дням:
- xlsx — дни, которые покрывает видимый отчёт по начислениям (весь его период), строки этого отчёта;
- api — дни, за которые начисления из API загружены (accrual_days);
- auto — за каждый день: есть отчёт XLSX → он (совпадает с кабинетом Ozon), иначе API, иначе нет данных.
Нет данных за день — пропуск, а не 0 (правило 12). Отчёты о реализации в период не складываются:
начислений по дням в них нет, их открывают как отчёт (шаг 3.1).
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.xlsx.common import norm
from app.integrations.xlsx.parsers.base import MISSING_COST_WARNING
from app.models.accrual import SOURCE_API, SOURCE_XLSX, Accrual, AccrualDay, AccrualType
from app.models.product import Product
from app.models.report import Report
from app.schemas.report import ArticleSales, MissingCost, NormalizedReportData
from app.services.costs import CostHistory

REVENUE = "выручка"  # строки, по которым считается себестоимость проданного (как в отчёте по начислениям)
ZERO = Decimal(0)
Source = str  # auto | xlsx | api


def days_between(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]


def ranges(days: list[date]) -> list[tuple[date, date]]:
    """Отсортированные дни → непрерывные периоды [начало, конец]."""
    out: list[tuple[date, date]] = []
    for day in sorted(days):
        if out and out[-1][1] + timedelta(days=1) == day:
            out[-1] = (out[-1][0], day)
        else:
            out.append((day, day))
    return out


@dataclass
class Coverage:
    date_from: date
    date_to: date
    days: dict[date, str | None]  # день → xlsx | api | None (нет данных)
    report_of_day: dict[date, Report] = field(default_factory=dict)  # для дней из XLSX — чей отчёт
    realization: list[Report] = field(default_factory=list)  # отчёты о реализации, пересекающие период

    def count(self, source: str | None) -> int:
        return sum(s == source for s in self.days.values())

    @property
    def missing(self) -> list[tuple[date, date]]:
        return ranges([d for d, s in self.days.items() if s is None])

    @property
    def data_source(self) -> str:
        used = {s for s in self.days.values() if s}
        return "none" if not used else "mixed" if len(used) > 1 else used.pop()

    @property
    def reports(self) -> list[Report]:
        seen: dict[uuid.UUID, Report] = {}
        for day, report in sorted(self.report_of_day.items()):
            if self.days[day] == SOURCE_XLSX:
                seen.setdefault(report.id, report)
        return list(seen.values())


async def coverage(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, source: Source) -> Coverage:
    res = await db.execute(
        select(Report).where(
            Report.user_id == user_id, Report.deleted_at.is_(None),
            Report.period_start <= date_to, Report.period_end >= date_from,
        )
    )
    reports = list(res.scalars())
    report_of_day: dict[date, Report] = {}
    # отчёты пересекаются (оставили оба) — за день берём загруженный позже
    for report in sorted((r for r in reports if r.report_type == "accrual"), key=lambda r: r.uploaded_at):
        for day in days_between(max(report.period_start, date_from), min(report.period_end, date_to)):
            report_of_day[day] = report
    res = await db.execute(
        select(AccrualDay.day)
        .where(AccrualDay.user_id == user_id, AccrualDay.day >= date_from, AccrualDay.day <= date_to)
    )
    api_days = set(res.scalars())
    days: dict[date, str | None] = {}
    for day in days_between(date_from, date_to):
        xlsx, api = day in report_of_day and source != SOURCE_API, day in api_days and source != SOURCE_XLSX
        days[day] = SOURCE_XLSX if xlsx else SOURCE_API if api else None
    realization = [r for r in reports if r.report_type == "realization"]
    return Coverage(date_from, date_to, days, report_of_day, realization)


def report_coverage(report: Report) -> Coverage:
    """Дни одного отчёта — его строки, даже если за эти дни есть отчёт, загруженный позже. У отчёта о реализации
    строк по дням нет."""
    days = days_between(report.period_start, report.period_end)
    source = SOURCE_XLSX if report.report_type == "accrual" else None
    return Coverage(report.period_start, report.period_end, {d: source for d in days}, {d: report for d in days})


@dataclass
class Line:
    day: date
    type_name: str
    amount: Decimal
    quantity: int | None
    offer_id: str | None
    sku: str
    name: str


async def load_lines(db: AsyncSession, user_id: uuid.UUID, cov: Coverage) -> list[Line]:
    lines: list[Line] = []
    xlsx_reports = {r.id for r in cov.reports}
    if xlsx_reports:
        res = await db.execute(
            select(Accrual, Product.name)
            .outerjoin(Product, Product.id == Accrual.product_id)
            .where(
                Accrual.user_id == user_id, Accrual.source == SOURCE_XLSX, Accrual.report_id.in_(xlsx_reports),
                Accrual.accrual_date >= cov.date_from, Accrual.accrual_date <= cov.date_to,
            )
        )
        for a, product_name in res.tuples():
            report = cov.report_of_day.get(a.accrual_date)
            if cov.days.get(a.accrual_date) == SOURCE_XLSX and report is not None and report.id == a.report_id:
                raw = a.raw_json or {}
                name = product_name or raw.get("Название товара") or raw.get("название товара") or ""
                lines.append(_line(a, a.type_name or "", a.offer_id, str(name)))
    if any(s == SOURCE_API for s in cov.days.values()):
        res = await db.execute(
            select(Accrual, AccrualType.name_ru, Product.offer_id, Product.name)
            .outerjoin(AccrualType, AccrualType.id == Accrual.accrual_type_id)
            .outerjoin(Product, Product.id == Accrual.product_id)
            .where(
                Accrual.user_id == user_id, Accrual.source == SOURCE_API,
                Accrual.accrual_date >= cov.date_from, Accrual.accrual_date <= cov.date_to,
            )
        )
        for a, type_name, offer_id, product_name in res.tuples():
            if cov.days.get(a.accrual_date) == SOURCE_API:
                name = type_name or a.type_name or f"Тип начисления {a.accrual_type_id}"
                lines.append(_line(a, name, offer_id, product_name or ""))
    return lines


def _line(a: Accrual, type_name: str, offer_id: str | None, name: str) -> Line:
    return Line(
        day=a.accrual_date, type_name=type_name, amount=Decimal(a.amount), quantity=a.quantity,
        offer_id=offer_id or None, sku=str(a.sku) if a.sku else "", name=name,
    )


def is_revenue(line: Line) -> bool:
    return norm(line.type_name) == REVENUE


@dataclass
class PeriodData:
    data: NormalizedReportData
    article_vat: dict[str, Decimal]  # НДС поставщика у артикула — средний по строкам, если менялся
    cost_with_vat: dict[date, Decimal]  # себестоимость с НДС по дням — для P&L по дням


def build(
    lines: list[Line], date_from: date, date_to: date, costs: CostHistory, default_vat: Decimal
) -> PeriodData:
    """Строки периода → данные в виде отчёта по начислениям. Себестоимость — на дату каждой строки «Выручка»."""
    by_type: dict[str, Decimal] = {}
    articles: dict[str, dict] = {}
    missing: dict[str, dict] = {}
    cost_with_vat: dict[date, Decimal] = {}
    revenue_rows = rows_without_article = 0
    for ln in lines:
        by_type[ln.type_name] = by_type.get(ln.type_name, ZERO) + ln.amount
        if not is_revenue(ln):
            continue
        revenue_rows += 1
        if not ln.offer_id:
            rows_without_article += 1
            continue
        qty = ln.quantity or 1  # как в отчёте: пусто или 0 — одна штука
        a = articles.setdefault(
            ln.offer_id, {"name": ln.name, "sku": ln.sku, "qty": 0, "revenue": ZERO, "cost": ZERO, "with_vat": ZERO,
                          "known": 0}
        )
        a["revenue"] += ln.amount
        a["qty"] += qty
        rec = costs.record_at(ln.offer_id, ln.day)
        if rec is None:
            m = missing.setdefault(ln.offer_id, {"article": ln.offer_id, "sku": ln.sku, "name": ln.name, "qty": 0})
            m["qty"] += qty
            continue
        cost = Decimal(rec.cost_price) * qty
        vat = Decimal(rec.vat_multiplier) if rec.vat_multiplier is not None else default_vat
        a["cost"] += cost
        a["with_vat"] += cost * vat
        a["known"] += qty
        cost_with_vat[ln.day] = cost_with_vat.get(ln.day, ZERO) + cost * vat

    by_article: dict[str, ArticleSales] = {}
    article_vat: dict[str, Decimal] = {}
    for offer, a in articles.items():
        has_cost = a["known"] > 0
        unit = a["cost"] / a["known"] if has_cost else None
        by_article[offer] = ArticleSales(
            name=a["name"], sku=a["sku"], qty=a["known"] if has_cost else a["qty"], revenue=a["revenue"],
            cost=unit, total_cost=a["cost"] if has_cost else None, has_cost=has_cost,
        )
        if has_cost and a["cost"]:
            article_vat[offer] = a["with_vat"] / a["cost"]

    warnings: list[str] = []
    if missing:
        qty = sum(m["qty"] for m in missing.values())
        warnings.append(
            f"{MISSING_COST_WARNING} {len(missing)} артикулов ({qty} шт.) — себестоимость занижена, прибыль "
            "завышена. Добавьте их себестоимость в справочник."
        )
    if rows_without_article:
        warnings.append(
            f"Строк «Выручка» без артикула: {rows_without_article} — себестоимость по ним не учтена, прибыль завышена."
        )
    data = NormalizedReportData(
        report_type="accrual",
        period=f"{date_from:%d.%m.%Y}-{date_to:%d.%m.%Y}",
        period_start=date_from,
        period_end=date_to,
        auto_total=sum(by_type.values(), ZERO),
        revenue_base=sum((ln.amount for ln in lines if is_revenue(ln)), ZERO),
        cost_sales=sum((a["cost"] for a in articles.values()), ZERO),
        by_type=by_type,
        by_article=by_article,
        missing_cost=[MissingCost(**m) for m in missing.values()],
        warnings=warnings,
        sheet_name="",
        header_row=0,
        rows_count=revenue_rows,
    )
    return PeriodData(data, article_vat, cost_with_vat)


def manual_adjustment(cov: Coverage) -> tuple[Decimal, list[Report]]:
    """Ручные суммы «сколько пришло» из отчётов, целиком вошедших в период и взятых из XLSX за все свои дни:
    разница с суммой по отчёту добавляется к «пришло от Ozon» за период."""
    delta, used = ZERO, []
    for report in cov.reports:
        if report.manual_total is None:
            continue
        inside = cov.date_from <= report.period_start and report.period_end <= cov.date_to
        own_days = all(
            cov.days.get(d) == SOURCE_XLSX and cov.report_of_day.get(d) is report
            for d in days_between(report.period_start, report.period_end)
        )
        if inside and own_days:
            delta += Decimal(report.manual_total) - Decimal(report.summary_json["auto_total"])
            used.append(report)
    return delta, used
