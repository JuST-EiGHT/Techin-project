"""P&L отчёта Ozon (шаг 3.1). Чистый модуль — без БД, HTTP и FastAPI.

Формулы — docs/BUSINESS_LOGIC.md §3:
    effective_total   = manual_total, если задан, иначе auto_total — сколько пришло от Ozon
    owed              = Σ себестоимость × количество × НДС поставщика — себестоимость с НДС
    profit_before_tax = effective_total − owed
    налог и НДС       — engine/tax.py по налоговому профилю на дату периода (шаг 2.5)
    net               = effective_total − owed − налог − НДС — остаток
    margin            = net / revenue_base × 100; revenue_base = 0 → None
Деньги — Decimal, округление до копеек один раз, в конце (так считает эталонный калькулятор).
Налог не задан (нет профиля) — налог, остаток и маржа None: не 0 и не чужие 20 % (правила 12, 13).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.engine.tax import Profile, compute_tax, report_tax_input
from app.integrations.xlsx.parsers.base import MISSING_COST_WARNING
from app.schemas.report import MissingCost, NormalizedReportData

CENT = Decimal("0.01")
ZERO = Decimal(0)
HUNDRED = Decimal(100)
OWED_LIMIT = 3  # себестоимость с НДС больше суммы от Ozon в 3 раза — скорее ошибка в данных

TAX_NOT_SET = "Налог не задан — укажите систему налогообложения в «Налоги и НДС»: без неё остаток и маржа не считаются."


@dataclass(frozen=True)
class PnLParams:
    tax_profile: Profile | None  # действующий на дату периода (или поправка отчёта); None — налог не задан
    supplier_vat: Decimal  # НДС поставщика по умолчанию, коэффициентом: 1.22 — 22 %, 1 — без НДС
    article_vat: Mapping[str, Decimal] = field(default_factory=dict)  # свой НДС у артикула (product_costs)
    manual_total: Decimal | None = None  # «сколько реально пришло»; None — по отчёту
    day: date | None = None  # на какую дату ставки; по умолчанию — начало периода отчёта
    data_source: str = "xlsx"  # xlsx | api


@dataclass(frozen=True)
class PnLIssue:
    code: str  # no_revenue | no_items | missing_cost | owed_too_big
    message: str


@dataclass
class PnLResult:
    auto_total: Decimal
    effective_total: Decimal
    revenue_base: Decimal
    cost_sales: Decimal
    owed: Decimal  # себестоимость с НДС поставщика (долг поставщику)
    profit_before_tax: Decimal
    tax: Decimal | None  # None — налог не задан
    vat: Decimal | None  # НДС к уплате; 0 — не платит; None — налог не задан
    tax_lines: list[str]  # расчёт налога по шагам (engine/tax)
    net: Decimal | None  # остаток
    margin: Decimal | None  # % от revenue_base; None — выручки нет или налог не задан
    pct_of_revenue: dict[str, Decimal | None]  # owed, profit_before_tax, tax, vat, net — % от revenue_base
    by_type: dict[str, Decimal]
    by_type_pct: dict[str, Decimal]  # доходы — от суммы доходов, расходы — от суммы расходов: каждая группа 100 %
    missing_cost: list[MissingCost]
    warnings: list[str]  # проверки P&L и налога (предупреждения разбора — в отчёте)
    data_source: str


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


def _share(part: Decimal, whole: Decimal) -> Decimal | None:
    return None if whole == 0 else (part / whole * HUNDRED).quantize(CENT, ROUND_HALF_UP)


def _percents(amounts: dict[str, Decimal]) -> dict[str, Decimal]:
    """Доли группы в процентах, сумма ровно 100,00: остаток от округления — самым большим дробным частям."""
    total = sum(amounts.values(), ZERO)
    if total == 0:
        return {name: ZERO for name in amounts}
    exact = {name: abs(a) / abs(total) * HUNDRED for name, a in amounts.items()}
    rounded = {name: v.quantize(CENT, rounding="ROUND_DOWN") for name, v in exact.items()}
    left = int((HUNDRED - sum(rounded.values(), ZERO)) / CENT)
    for name in sorted(exact, key=lambda n: exact[n] - rounded[n], reverse=True)[:left]:
        rounded[name] += CENT
    return rounded


def by_type_percents(by_type: dict[str, Decimal]) -> dict[str, Decimal]:
    incomes = {name: Decimal(a) for name, a in by_type.items() if Decimal(a) > 0}
    expenses = {name: Decimal(a) for name, a in by_type.items() if Decimal(a) < 0}
    return {**_percents(incomes), **_percents(expenses), **{n: ZERO for n, a in by_type.items() if Decimal(a) == 0}}


class PnLEngine:
    def owed(self, data: NormalizedReportData, params: PnLParams) -> Decimal:
        """Себестоимость с НДС: свой НДС у артикула, иначе НДС поставщика по умолчанию."""
        known = [(art, a) for art, a in data.by_article.items() if a.has_cost and a.cost is not None]
        if not known:
            return data.cost_sales * params.supplier_vat
        return sum((a.cost * a.qty * params.article_vat.get(art, params.supplier_vat) for art, a in known), ZERO)

    def validate(self, data: NormalizedReportData, owed: Decimal, effective_total: Decimal) -> list[PnLIssue]:
        issues: list[PnLIssue] = []
        if data.revenue_base == 0:
            issues.append(PnLIssue("no_revenue", "В отчёте нет выручки — маржу не посчитать."))
        if not data.by_article:
            issues.append(PnLIssue("no_items", "В отчёте нет товаров — себестоимость не посчитать."))
        if data.missing_cost:
            qty = sum(m.qty for m in data.missing_cost)
            issues.append(
                PnLIssue(
                    "missing_cost",
                    f"{MISSING_COST_WARNING} {len(data.missing_cost)} артикулов ({qty} шт.) — себестоимость "
                    "занижена, прибыль завышена.",
                )
            )
        if effective_total > 0 and owed > effective_total * OWED_LIMIT:
            issues.append(
                PnLIssue(
                    "owed_too_big",
                    f"Себестоимость с НДС больше суммы от Ozon в {OWED_LIMIT} с лишним раза — проверьте "
                    "себестоимость и сумму от Ozon.",
                )
            )
        return issues

    def compute(self, data: NormalizedReportData, params: PnLParams) -> PnLResult:
        effective = params.manual_total if params.manual_total is not None else data.auto_total
        owed = self.owed(data, params)
        profit = effective - owed
        warnings: list[str] = []
        for issue in self.validate(data, owed, effective):
            # про себестоимость уже предупредил разбор отчёта — второй раз не повторяем
            if issue.code == "missing_cost" and any(w.startswith(MISSING_COST_WARNING) for w in data.warnings):
                continue
            warnings.append(issue.message)

        tax = vat = net = margin = None
        tax_lines: list[str] = []
        if params.tax_profile is None:
            warnings.append(TAX_NOT_SET)
        else:
            day = params.day or data.period_start or date.today()
            result = compute_tax(params.tax_profile, report_tax_input(data, owed, effective), day)
            tax_lines, warnings = result.lines, warnings + result.warnings
            net_exact = profit - result.tax_exact - result.vat_exact
            tax, vat, net = result.tax, result.vat, _money(net_exact)
            margin = _share(net_exact, data.revenue_base)

        base = data.revenue_base
        return PnLResult(
            auto_total=_money(data.auto_total),
            effective_total=_money(effective),
            revenue_base=_money(base),
            cost_sales=_money(data.cost_sales),
            owed=_money(owed),
            profit_before_tax=_money(profit),
            tax=tax,
            vat=vat,
            tax_lines=tax_lines,
            net=net,
            margin=margin,
            pct_of_revenue={
                "owed": _share(owed, base),
                "profit_before_tax": _share(profit, base),
                "tax": None if tax is None else _share(tax, base),
                "vat": None if vat is None else _share(vat, base),
                "net": None if net is None else margin,
            },
            by_type=dict(data.by_type),
            by_type_pct=by_type_percents(data.by_type),
            missing_cost=list(data.missing_cost),
            warnings=warnings,
            data_source=params.data_source,
        )
