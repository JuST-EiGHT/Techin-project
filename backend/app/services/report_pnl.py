"""P&L отчёта Ozon для API (шаг 3.1): параметры движка из данных пользователя.

- налог — по налоговому профилю на дату начала периода (шаг 2.5); поправка в отчёте (tax_rate, tax_base)
  — «Свой процент» только для этого отчёта;
- НДС поставщика — поправка в отчёте (vat_multiplier), иначе из профиля, иначе общая ставка;
  у артикула со своей ставкой в справочнике себестоимости — его ставка (на ту же дату, что и цена);
- «сколько реально пришло» — manual_total отчёта.
Контекст (профили и себестоимость) загружается один раз на запрос — и для списка отчётов.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.pnl import PnLEngine, PnLParams, PnLResult
from app.engine.tax import Profile, pct
from app.models.report import Report
from app.models.tax_profile import TaxProfile
from app.schemas.report import NormalizedReportData
from app.services.costs import CostHistory, load_cost_history
from app.services.report_costs import cost_date
from app.services.tax_profile import default_supplier_vat_pct, load_profiles, profile_on, profile_title

ENGINE = PnLEngine()


@dataclass
class PnLContext:
    profiles: list[TaxProfile]
    costs: CostHistory


@dataclass
class ReportPnL:
    result: PnLResult
    tax_title: str | None  # по какому профилю посчитан налог; None — налог не задан
    supplier_vat: Decimal  # НДС поставщика по умолчанию для этого отчёта, коэффициентом


async def load_context(db: AsyncSession, user_id: uuid.UUID) -> PnLContext:
    return PnLContext(await load_profiles(db, user_id), await load_cost_history(db, user_id))


def report_pnl(data: NormalizedReportData, ctx: PnLContext, report: Report | None = None) -> ReportPnL:
    day = cost_date(data.period_start)  # та же дата, на которую взята себестоимость
    row = profile_on(ctx.profiles, day)
    profile = row.to_engine() if row else None
    title = profile_title(row) if row else None
    if report is not None and report.tax_rate is not None:  # поправка в отчёте — «свой процент»
        rate = (Decimal(report.tax_rate) * 100).quantize(Decimal("0.01"))
        base = report.tax_base or "profit"
        profile = Profile("custom", row.legal_form if row else None, rate_pct=rate, custom_base=base)
        title = (
            f"Свой процент: {pct(rate)} {'от дохода' if base == 'revenue' else 'от прибыли'} — поправка в этом отчёте"
        )

    if report is not None and report.vat_multiplier is not None:
        supplier = Decimal(report.vat_multiplier)
    else:
        supplier_pct = Decimal(row.supplier_vat_pct) if row else default_supplier_vat_pct(day)
        supplier = 1 + supplier_pct / 100
    article_vat = {
        article: Decimal(rec.vat_multiplier)
        for article in data.by_article
        if (rec := ctx.costs.record_at(article, day)) is not None and rec.vat_multiplier is not None
    }
    params = PnLParams(
        tax_profile=profile,
        supplier_vat=supplier,
        article_vat=article_vat,
        manual_total=report.manual_total if report is not None else None,
        day=day,
    )
    return ReportPnL(ENGINE.compute(data, params), title, supplier.quantize(Decimal("0.001")))
