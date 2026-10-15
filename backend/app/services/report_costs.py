"""Себестоимость в итогах отчётов: пересчёт без повторного разбора файла.

Итоги отчёта (summary_json) — производные (правило 12): cost_sales, by_article.cost и
missing_cost пересобираются из количеств по артикулам и справочника себестоимости на дату
отчёта (начало периода). Так отчёт подхватывает новую цену, а прошлые отчёты не меняются от
цены, введённой с более поздней даты.
"""
import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.xlsx.costs import CostProvider, costs_on
from app.integrations.xlsx.parsers.base import MISSING_COST_WARNING, SalesLedger, money
from app.models.report import Report
from app.schemas.report import NormalizedReportData
from app.services.costs import CostHistory, load_cost_history
from app.services.sync.finance import today_msk

ROW_KIND = {"accrual": "«Выручка»", "realization": "таблицы"}


def cost_date(period_start: date | None, fallback: date | None = None) -> date:
    """Себестоимость отчёта берётся на дату начала его периода."""
    return period_start or fallback or today_msk()


def apply_costs(data: NormalizedReportData, costs: CostProvider) -> NormalizedReportData:
    """Пересчитать себестоимость отчёта по справочнику. Выручка и количества не меняются."""
    ledger = SalesLedger(costs_on(costs, cost_date(data.period_start)))
    for article, a in data.by_article.items():
        ledger.add(article=article, sku=a.sku, name=a.name, qty=a.qty, revenue=a.revenue)
    warnings = [w for w in data.warnings if not w.startswith(MISSING_COST_WARNING)]
    warnings[:0] = ledger.warnings(ROW_KIND[data.report_type])  # предупреждение о себестоимости — первым
    return data.model_copy(update={
        "cost_sales": money(ledger.cost_sales),
        "by_article": ledger.by_article(),
        "missing_cost": ledger.missing_cost(),
        "warnings": warnings,
    })


async def recost_reports(
    db: AsyncSession, user_id: uuid.UUID, offer_ids: Iterable[str] | None = None, history: CostHistory | None = None
) -> int:
    """Пересчитать себестоимость видимых отчётов, где есть эти артикулы (None — во всех).
    Возвращает, сколько отчётов изменилось. Коммит — на вызывающем."""
    wanted = set(offer_ids) if offer_ids is not None else None
    history = history or await load_cost_history(db, user_id)
    reports = (await db.execute(
        select(Report).where(Report.user_id == user_id, Report.deleted_at.is_(None))
    )).scalars()
    changed = 0
    for report in reports:
        by_article = report.summary_json.get("by_article") or {}
        if wanted is not None and not wanted.intersection(by_article):
            continue
        before = NormalizedReportData.model_validate(report.summary_json)
        after = apply_costs(before, history)
        if after != before:
            report.summary_json = after.model_dump(mode="json")
            changed += 1
    return changed
