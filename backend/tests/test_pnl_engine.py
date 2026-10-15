"""P&L engine (шаг 3.1): эталоны BUSINESS_LOGIC §3 до копейки, ручная сумма, налог по профилю."""

from decimal import Decimal as D
from pathlib import Path

import pytest

from app.engine.pnl import TAX_NOT_SET, PnLEngine, PnLParams, by_type_percents
from app.engine.tax import Profile
from app.integrations.xlsx import parse_report
from app.integrations.xlsx.costs import MappingCostProvider
from app.schemas.report import NormalizedReportData
from tests.conftest import reference_costs

FIXTURES = Path(__file__).parent / "fixtures"
OWNER = Profile("custom", "ip", rate_pct=D(20), custom_base="profit")  # параметры владельца — явно
VAT_122 = D("1.22")
engine = PnLEngine()


def report(name: str, costs=None) -> NormalizedReportData:
    return parse_report((FIXTURES / name).read_bytes(), reference_costs() if costs is None else costs, name)


@pytest.fixture(scope="module")
def week():
    return report("accrual_2026-09-07_13.xlsx")


@pytest.fixture(scope="module")
def month():
    return report("realization_2026-08.xlsx")


def test_reference_accrual_week(week):
    r = engine.compute(week, PnLParams(OWNER, VAT_122))
    assert (r.auto_total, r.effective_total, r.revenue_base, r.cost_sales) == (
        D("10955.83"),
        D("10955.83"),
        D("23712.03"),
        D("10143.36"),
    )
    assert (r.owed, r.profit_before_tax, r.tax, r.net) == (D("12374.90"), D("-1419.07"), D("0.00"), D("-1419.07"))
    assert r.vat == 0 and r.margin == D("-5.98") and r.missing_cost == []
    assert len(week.by_article) == 16
    assert r.data_source == "xlsx" and r.tax_lines[0].startswith("Прибыль: пришло от Ozon 10 955,83 ₽")


def test_reference_realization_month(month):
    r = engine.compute(month, PnLParams(OWNER, VAT_122))
    assert (r.auto_total, r.revenue_base, r.cost_sales) == (D("37803.35"), D("37461.65"), D("14684.89"))
    # округление один раз, в конце — как в калькуляторе: 15 910,23, а не 19 887,78 − 3 977,56 = 15 910,22
    assert (r.owed, r.profit_before_tax, r.tax, r.net) == (D("17915.57"), D("19887.78"), D("3977.56"), D("15910.23"))
    assert r.margin == D("42.47") and r.pct_of_revenue["net"] == D("42.47")
    assert r.pct_of_revenue["owed"] == D("47.82") and r.pct_of_revenue["tax"] == D("10.62")


def test_manual_total_overrides_auto(month):
    r = engine.compute(month, PnLParams(OWNER, VAT_122, manual_total=D("37000")))
    assert (r.auto_total, r.effective_total) == (D("37803.35"), D("37000.00"))
    assert (r.profit_before_tax, r.tax, r.net) == (D("19084.43"), D("3816.89"), D("15267.55"))


def test_negative_profit_tax_zero(week):
    r = engine.compute(week, PnLParams(OWNER, VAT_122, manual_total=D("1000")))
    assert r.profit_before_tax < 0 and r.tax == 0 and r.net == r.profit_before_tax


def test_no_tax_profile_means_not_set(week):
    r = engine.compute(week, PnLParams(None, VAT_122))
    assert (r.tax, r.vat, r.net, r.margin) == (None, None, None, None)
    assert r.profit_before_tax == D("-1419.07") and TAX_NOT_SET in r.warnings  # до налога — считаем
    assert r.pct_of_revenue["net"] is None and r.pct_of_revenue["owed"] == D("52.19")


def test_tax_by_profile_and_vat(week):
    # УСН 6 % с НДС 5 %: налог — с полной цены продаж (41 252,00), остаток — за вычетом налога и НДС
    p = Profile("usn_income", "ip", rate_pct=D(6), vat_mode="special", vat_rate_pct=D(5))
    r = engine.compute(week, PnLParams(p, VAT_122))
    assert (r.tax, r.vat) == (D("2357.26"), D("1964.38"))
    assert r.net == D("-5740.71")  # 10 955,83 − 12 374,90 − 2 357,26 − 1 964,38
    assert any(line.startswith("НДС 5 % в цене продаж") for line in r.tax_lines)


def test_article_vat_overrides_default(week):
    # у одного артикула поставщик без НДС: 164 × 2 шт. без коэффициента
    r = engine.compute(week, PnLParams(OWNER, VAT_122, article_vat={"6001-2RS.C3.SRL.NAC.TH": D(1)}))
    assert r.owed == D("12302.74")  # 12 374,90 − 328 × 0,22
    assert engine.compute(week, PnLParams(OWNER, D(1))).owed == D("10143.36")  # поставщик без НДС


def test_supplier_vat_from_report_without_articles():
    data = NormalizedReportData(
        report_type="accrual",
        period="",
        period_start=None,
        period_end=None,
        auto_total=D(100),
        revenue_base=D(0),
        cost_sales=D(10),
        by_type={},
        by_article={},
        missing_cost=[],
        warnings=[],
        sheet_name="",
        header_row=1,
        rows_count=0,
    )
    r = engine.compute(data, PnLParams(OWNER, VAT_122))
    assert r.owed == D("12.20") and r.margin is None  # выручки нет — маржа None, а не деление на ноль
    codes = [i.code for i in engine.validate(data, D("12.2"), D(100))]
    assert codes == ["no_revenue", "no_items"]
    assert any("нет выручки" in w for w in r.warnings) and any("нет товаров" in w for w in r.warnings)


def test_validate_missing_cost_and_owed_too_big():
    data = report("accrual_2026-09-07_13.xlsx", costs=MappingCostProvider({}))  # себестоимости нет совсем
    r = engine.compute(data, PnLParams(OWNER, VAT_122))
    assert r.owed == 0 and len(r.missing_cost) == 16
    assert "missing_cost" in [i.code for i in engine.validate(data, D(0), D(1))]
    # о себестоимости уже предупредил разбор — в P&L второй раз не повторяем
    assert not any(w.startswith("Не найдена себестоимость") for w in r.warnings)
    issues = engine.validate(report("accrual_2026-09-07_13.xlsx"), D("40000"), D("10000"))
    assert [i.code for i in issues] == ["owed_too_big"]


def test_by_type_percents_each_group_100(week, month):
    for data in (week, month):
        pct = engine.compute(data, PnLParams(OWNER, VAT_122)).by_type_pct
        incomes = [pct[n] for n, a in data.by_type.items() if a > 0]
        expenses = [pct[n] for n, a in data.by_type.items() if a < 0]
        assert sum(incomes) == D("100.00")
        if expenses:
            assert sum(expenses) == D("100.00")
    pct = by_type_percents(week.by_type)
    # доход — от суммы положительных (BUSINESS_LOGIC §3): 23 712,03 из 46 225,90 (с «Возвратом вознаграждения»)
    assert pct["Выручка"] == D("51.29")
    # остатки от округления раздаются так, чтобы сумма была ровно 100
    assert sum(by_type_percents({"a": D(1), "b": D(1), "c": D(1)}).values()) == D("100.00")
    assert by_type_percents({"a": D(0)}) == {"a": D(0)}
