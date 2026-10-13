"""Расчёт налога и НДС по налоговому профилю (шаг 2.5): примеры docs/TAXES.md и эталоны BUSINESS_LOGIC §3."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.engine.tax import (
    REGIMES,
    Profile,
    TaxInput,
    compute_tax,
    default_rate,
    how,
    limits,
    min_rate,
    report_tax_input,
    split_accruals,
    vat_rates,
)
from app.integrations.xlsx import parse_report
from tests.conftest import reference_costs

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 9, 7)


def data(income, ozon=0, cost=0, cost_with_vat=None, payout=None, revenue=None, days=30, known=True) -> TaxInput:
    income, ozon, cost = D(str(income)), D(str(ozon)), D(str(cost))
    return TaxInput(
        income=income,
        ozon_expenses=ozon,
        cost_sales=cost,
        cost_with_vat=cost if cost_with_vat is None else D(str(cost_with_vat)),
        payout=income - ozon if payout is None else D(str(payout)),
        revenue_base=income if revenue is None else D(str(revenue)),
        days=days,
        ozon_expenses_known=known,
    )


def tax(profile: Profile, d: TaxInput):
    return compute_tax(profile, d, DAY)


def report(name: str):
    return parse_report((FIXTURES / name).read_bytes(), reference_costs(), name)


# ---------- справочник ----------


def test_rates_and_limits_from_taxes_md():
    assert default_rate("usn_income", "ip", DAY) == 6 and default_rate("usn_income_expense", "ooo", DAY) == 15
    assert min_rate("usn_income_expense", DAY) == 1 and min_rate("ausn_income_expense", DAY) == 3
    assert min_rate("usn_income", DAY) is None
    assert default_rate("ausn_income", "ip", DAY) == 8 and default_rate("ausn_income_expense", "ip", DAY) == 20
    assert default_rate("osn", "ooo", DAY) == 25 and default_rate("osn", "ip", DAY) == 13
    assert default_rate("npd", "self_employed", DAY) == 4 and default_rate("eshn", "ooo", DAY) == 6
    assert default_rate("custom", "ip", DAY) is None and default_rate("psn", "ip", DAY) is None
    assert vat_rates("special", DAY) == [5, 7] and vat_rates("general", DAY) == [22, 10, 0]
    assert limits(DAY)["usn_vat_free_income"] == 20_000_000 and limits(DAY)["usn_income"] == 490_500_000
    # дата раньше первой записи — первая запись (как у себестоимости)
    assert default_rate("usn_income", "ip", date(2024, 1, 1)) == 6
    assert set(REGIMES["osn"].forms) == {"ip", "ooo"} and REGIMES["psn"].forms == ("ip",)
    assert REGIMES["npd"].restriction and "собственного производства" in REGIMES["npd"].restriction


# ---------- доход: полная цена продажи, а не выплата ----------


def test_example_income_is_full_sale_price_not_payout():
    # docs/TAXES.md: продали за 2 500 ₽, комиссия 500 ₽, на счёт пришло 2 000 ₽ → доход 2 500, расход 500
    d = data(2500, ozon=500, cost=1000)
    assert tax(Profile("usn_income", "ip"), d).tax == D("150.00")  # 6 % от 2 500, а не от 2 000
    r = tax(Profile("usn_income_expense", "ip"), d)
    assert (r.base, r.tax) == (D("1000.00"), D("150.00"))  # (2 500 − 500 − 1 000) × 15 %
    assert r.lines[0] == "Доход: 2 500,00 ₽"


def test_example_ausn_income():
    # docs/TAXES.md: покупатель оплатил 4 000 ₽, Ozon удержал 800 ₽ → доход 4 000
    assert tax(Profile("ausn_income", "ooo"), data(4000, ozon=800)).tax == D("320.00")


def test_minimum_tax():
    r = tax(Profile("usn_income_expense", "ooo"), data(2500, ozon=500, cost=2400))  # убыток
    assert r.base == D("-400.00") and r.tax == D("25.00")  # 1 % дохода
    assert any("минимального налога 1 %" in line for line in r.lines)
    assert tax(Profile("ausn_income_expense", "ip"), data(10000, ozon=3000, cost=7000)).tax == D("300.00")  # 3 %


def test_split_accruals_of_reference_report():
    d = report("accrual_2026-09-07_13.xlsx")
    income, expenses = split_accruals(d.by_type)
    # 23 712,03 выручка + 19 926,39 баллы + 201,76 партнёры − 2 588,18 возврат выручки
    assert income == D("41252.00")
    assert income - expenses == d.auto_total  # доход − удержания Ozon = сколько пришло
    t = report_tax_input(d, D("12374.90"))
    assert (t.income, t.ozon_expenses, t.days, t.ozon_expenses_known) == (D("41252.00"), D("30296.17"), 7, True)
    assert tax(Profile("usn_income", "ip"), t).tax == D("2475.12")


def test_realization_report_has_no_ozon_expenses():
    d = report("realization_2026-08.xlsx")
    t = report_tax_input(d, D("17915.57"))
    assert t.income == d.auto_total == D("37803.35") and t.days == 31 and not t.ozon_expenses_known
    assert tax(Profile("usn_income", "ip"), t).warnings == []  # «Доходы» — расходы не нужны
    r = tax(Profile("usn_income_expense", "ip"), t)
    assert r.warnings and r.warnings[0].startswith("В отчёте о реализации нет удержаний Ozon")


# ---------- «свой процент» = эталоны калькулятора ----------


def test_custom_percent_matches_reference_values():
    owner = Profile("custom", "ip", rate_pct=D(20), custom_base="profit")
    week = report_tax_input(report("accrual_2026-09-07_13.xlsx"), D("12374.90"))
    r = tax(owner, week)
    assert (r.base, r.tax, r.vat) == (D("-1419.07"), D("0.00"), D("0"))  # BUSINESS_LOGIC: убыток → налог 0
    month = report_tax_input(report("realization_2026-08.xlsx"), D("17915.57"))
    r = tax(owner, month)
    assert (r.base, r.tax) == (D("19887.78"), D("3977.56"))
    # ручная сумма «сколько пришло» меняет прибыль
    assert tax(owner, report_tax_input(report("realization_2026-08.xlsx"), D("17915.57"), D("37000"))).tax == D(
        "3816.89"
    )
    assert tax(Profile("custom", rate_pct=D(6), custom_base="revenue"), data(1000, revenue=900)).tax == D("54.00")


# ---------- НДС ----------


def test_usn_with_special_vat():
    # УСН 6 % и НДС 5 %: НДС входит в цену, вычетов нет, доход для налога — без НДС
    r = tax(Profile("usn_income", "ooo", vat_mode="special", vat_rate_pct=D(5)), data(10500, ozon=2000))
    assert (r.vat, r.base, r.tax) == (D("500.00"), D("10000.00"), D("600.00"))
    assert "НДС 5 % в цене продаж: 10 500,00 ₽ × 5/105 = 500,00 ₽" in r.lines
    # «доходы минус расходы»: НДС поставщика и услуг Ozon — в расходах
    r = tax(
        Profile("usn_income_expense", "ooo", vat_mode="special", vat_rate_pct=D(7)),
        data(10700, ozon=2440, cost=5000, cost_with_vat=6100),
    )
    assert (r.vat, r.base, r.tax) == (D("700.00"), D("1460.00"), D("219.00"))  # (10 000 − 2 440 − 6 100) × 15 %


def test_osn_with_general_vat_and_deductions():
    # ООО на ОСН, НДС 22 %: к уплате = НДС с продаж − НДС поставщика − НДС в услугах Ozon
    p = Profile("osn", "ooo", vat_mode="general", vat_rate_pct=D(22))
    r = tax(p, data(12200, ozon=2440, cost=5000, cost_with_vat=6100))
    # 2 200 − 1 100 − 440 = 660; база 10 000 − 2 000 − 5 000 = 3 000; налог на прибыль 25 %
    assert (r.vat, r.base, r.tax) == (D("660.00"), D("3000.00"), D("750.00"))
    # вычеты больше начисленного — НДС 0 и предупреждение
    r = tax(p, data(1220, ozon=2440, cost=5000, cost_with_vat=6100))
    assert r.vat == 0 and r.warnings[0].startswith("НДС к вычету больше начисленного")
    # ИП на ОСН — НДФЛ, ставка по умолчанию 13 %
    assert tax(
        Profile("osn", "ip", vat_mode="general", vat_rate_pct=D(22)),
        data(12200, ozon=2440, cost=5000, cost_with_vat=6100),
    ).tax == D("390.00")


# ---------- взносы, патент, НПД, ЕСХН ----------


def test_usn_income_reduced_by_contributions():
    d = data(100000, days=30)  # налог 6 000
    p = Profile("usn_income", "ip", contributions_year=D(73000))  # за 30 дней — 6 000
    r = tax(p, d)
    assert r.tax == D("0.00") and "страховые взносы за 30 дн." in r.lines[-2]
    assert tax(Profile("usn_income", "ip", contributions_year=D(73000), has_employees=True), d).tax == D("3000.00")
    assert tax(Profile("usn_income", "ooo", contributions_year=D(36500)), d).tax == D("3000.00")  # 3 000 ≤ 50 %
    # «доходы минус расходы»: взносы — в расходах
    r = tax(Profile("usn_income_expense", "ip", contributions_year=D(36500)), data(100000, ozon=40000, days=30))
    assert r.base == D("57000.00") and r.tax == D("8550.00")
    # на АУСН взносов нет — даже если указаны
    assert tax(Profile("ausn_income", "ip", contributions_year=D(73000)), d).tax == D("8000.00")


def test_patent_npd_eshn():
    assert tax(Profile("psn", "ip", patent_cost_year=D(36500)), data(100000, days=30)).tax == D("3000.00")
    assert tax(Profile("npd", "self_employed"), data(1000)).tax == D("40.00")
    assert tax(Profile("npd", "self_employed", rate_pct=D(6)), data(1000)).tax == D("60.00")
    assert tax(Profile("eshn", "ooo"), data(10000, ozon=2000, cost=3000)).tax == D("300.00")


def test_own_regional_rate():
    assert tax(Profile("usn_income", "ip", rate_pct=D(1)), data(10000)).tax == D("100.00")


# ---------- как считаем ----------


@pytest.mark.parametrize("key", list(REGIMES))
def test_how_explains_every_regime(key):
    form = REGIMES[key].forms[0]
    text = " ".join(how(Profile(key, form, rate_pct=D(10) if key == "custom" else None), DAY))
    assert text
    if key != "custom" and key != "psn":
        assert "полная цена продажи покупателю" in text


def test_how_texts():
    text = " ".join(how(Profile("usn_income", "ip"), DAY))
    assert "Налог = доход × 6 %" in text and "до 100 %" in text and "превысит 20 млн ₽" in text
    text = " ".join(how(Profile("usn_income_expense", "ooo"), DAY))
    assert "× 15 %" in text and "меньше 1 % от дохода" in text and "себестоимость с НДС поставщика" in text
    text = " ".join(how(Profile("osn", "ooo", vat_mode="general", vat_rate_pct=D(22)), DAY))
    assert "× 25 %" in text and "себестоимость без НДС" in text and "22/122" in text
    assert "5/105" in " ".join(how(Profile("usn_income", "ip", vat_mode="special", vat_rate_pct=D(5)), DAY))
    assert "HTML-калькулятор" in " ".join(how(Profile("custom", rate_pct=D(20)), DAY))
