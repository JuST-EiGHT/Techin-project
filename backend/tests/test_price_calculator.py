"""Калькулятор минимальной и целевой цены (шаг 4.2): формула BUSINESS_LOGIC §4, налоги по профилю — ручной пересчёт."""
from datetime import date
from decimal import Decimal

import pytest

from app.engine.price_calculator import (
    PriceParams,
    PriceUnreachable,
    UnitTax,
    breakdown,
    calculate,
    coefficients,
    margin_at,
    price_for_margin,
    unit_tax,
)
from app.engine.tax import Profile

D = Decimal
DAY = date(2026, 10, 15)
# себестоимость 164 ₽ без НДС, поставщик с НДС 22 %; комиссия 38 %, эквайринг 1,5 %, реклама 6 %, логистика 60 ₽
BASE = dict(cost=D("164"), supplier_vat=D("1.22"), commission_pct=D("38"), acquiring_pct=D("1.5"), ad_pct=D("6"),
            logistics=D("60"), target_margin_pct=D("20"))


def params(profile: Profile | None = None, **changes) -> PriceParams:
    return PriceParams(**{**BASE, **changes}, tax=unit_tax(profile, DAY))


def test_formula_without_tax():
    # P = (C × НДС + L) / (1 − комиссия − эквайринг − реклама − M) — BUSINESS_LOGIC §4
    # α = 1 − 0,38 − 0,015 − 0,06 = 0,545;  β = 164 × 1,22 + 60 = 260,08
    p = params()
    assert coefficients(p) == (D("0.545"), D("260.08"))
    r = calculate(p, D("600"), D("500"))
    assert r.min_price == D("477.22")  # 260,08 / 0,545 = 477,2110… → вверх до копейки
    assert r.target_price == D("753.86")  # 260,08 / (0,545 − 0,20) = 753,8550…
    # при 600 ₽: 600 − 228 − 9 − 36 − 60 − 200,08 = 66,92 ₽, маржа 66,92 / 600 = 11,15 %
    c = r.current
    assert (c.commission, c.acquiring, c.ad, c.logistics, c.cost, c.vat, c.income_tax) == (
        D("228.00"), D("9.00"), D("36.00"), D("60.00"), D("200.08"), D("0.00"), D("0.00"))
    assert (c.profit, c.margin, c.net) == (D("66.92"), D("11.15"), D("66.92"))
    # в акции за 500 ₽: 0,545 × 500 − 260,08 = 12,42 ₽ → 2,48 %
    assert (r.promo.profit, r.promo.margin) == (D("12.42"), D("2.48"))
    assert r.profitable is True and r.min_error is None and r.target_error is None
    assert unit_tax(None, DAY).notes and not unit_tax(None, DAY).known


def test_min_price_is_break_even():
    p = params()
    at_min = breakdown(p, price_for_margin(p, D(0)))
    assert D(0) <= at_min.profit < D("0.01")  # округление вверх — не в минус
    at_target = breakdown(p, price_for_margin(p, D(20)))
    assert at_target.margin >= D(20)
    assert calculate(p, D("450")).profitable is False  # ниже минимальной — в минус
    assert margin_at(p, None) is None and margin_at(p, D(0)) is None


def test_usn_income_is_share_of_price():
    # УСН «Доходы» 6 % — доля дохода в знаменателе: α = 0,545 − 0,06 = 0,485
    p = params(Profile("usn_income", "ip"))
    assert p.tax.title == "УСН «Доходы» 6 %" and p.tax.income_tax_pct == 6
    r = calculate(p, D("600"))
    assert r.min_price == D("536.25")  # 260,08 / 0,485 = 536,2474…
    assert r.target_price == D("912.57")  # 260,08 / 0,285 = 912,5614…
    assert (r.current.income_tax, r.current.profit, r.current.margin) == (D("36.00"), D("30.92"), D("5.15"))


def test_special_vat_and_income_tax():
    # НДС 5 % без вычетов: с продаж 600 × 5/105 = 28,57; налог 6 % — с дохода без НДС: 571,43 × 6 % = 34,29
    # α = 100/105 × (1 − 0,06) − 0,455 = 0,440238…;  минимальная — 260,08 / 0,440238 = 590,775… → 590,78
    p = params(Profile("usn_income", "ip", vat_mode="special", vat_rate_pct=D(5)))
    r = calculate(p, D("600"))
    assert r.min_price == D("590.78") and r.target_price == D("1082.60")
    c = r.current
    assert (c.vat, c.income_tax, c.profit, c.margin) == (D("28.57"), D("34.29"), D("4.06"), D("0.68"))


def test_general_vat_with_deductions():
    # ОСН, НДС 22 % с вычетом: всё без НДС — α = 100/122 × 0,545, β = 60 × 100/122 + 164 (поставщик без НДС).
    # При тех же условиях минимальная цена — та же 477,22 ₽: НДС проходит сквозь цену.
    p = params(Profile("osn", "ooo", vat_mode="general", vat_rate_pct=D(22)))
    assert p.tax.vat_deduct and p.tax.profit_tax_pct == 25
    r = calculate(p, D("600"))
    assert r.min_price == D("477.22") and r.target_price == D("864.06")
    # НДС к уплате: 600 × 22/122 − (200,08 − 164) − (228 + 9 + 36 + 60) × 22/122 = 108,20 − 36,08 − 60,05 = 12,07
    c = r.current
    assert (c.vat, c.profit, c.margin) == (D("12.07"), D("54.85"), D("9.14"))
    # налог на прибыль 25 % в цену не входит — только «останется после налога»
    assert (c.profit_tax, c.net) == (D("13.71"), D("41.14"))


def test_profit_taxes_do_not_change_min_price():
    plain = calculate(params(), D("600"))
    for profile in (Profile("custom", rate_pct=D(20)), Profile("usn_income_expense", "ooo"), Profile("psn", "ip")):
        r = calculate(params(profile), D("600"))
        assert (r.min_price, r.target_price) == (plain.min_price, plain.target_price)
    custom = calculate(params(Profile("custom", rate_pct=D(20))), D("600")).current
    assert (custom.profit_tax, custom.net) == (D("13.38"), D("53.54"))  # 66,92 × 20 % = 13,38
    assert any("Минимальный налог" in n for n in unit_tax(Profile("usn_income_expense", "ooo"), DAY).notes)
    assert any("Патент" in n for n in unit_tax(Profile("psn", "ip"), DAY).notes)
    revenue = unit_tax(Profile("custom", rate_pct=D(3), custom_base="revenue"), DAY)
    assert revenue.income_tax_pct == 3 and revenue.profit_tax_pct == 0


def test_storage_and_other_costs():
    # хранение 10 ₽ и упаковка 5 ₽ на штуку — в числителе: (260,08 + 15) / 0,545 = 504,73…
    r = calculate(params(storage=D("10"), other=D("5")), D("600"))
    assert r.min_price == D("504.74") and (r.current.storage, r.current.other) == (D("10.00"), D("5.00"))
    # поставщик без НДС: 164 + 60 = 224 → 224 / 0,545 = 411,0091… → 411,01
    assert calculate(params(supplier_vat=D(1))).min_price == D("411.01")


def test_unreachable_price():
    r = calculate(PriceParams(cost=D(100), supplier_vat=D(1), commission_pct=D(60), ad_pct=D(40)), D("500"))
    assert r.min_price is None and r.target_price is None
    assert r.min_error.startswith("Цена недостижима") and "100 %" in r.min_error
    assert r.current.profit == D("-100.00") and r.profitable is False
    r = calculate(params(target_margin_pct=D(60)))
    assert r.min_price == D("477.22") and r.target_price is None
    assert r.target_error.startswith("Маржа 60 % недостижима") and "54,5 %" in r.target_error
    with pytest.raises(PriceUnreachable):
        price_for_margin(params(), D("54.5"))
    assert calculate(params(target_margin_pct=None)).target_price is None  # целевая маржа не задана — нет и цены


def test_no_price_and_default_tax():
    r = calculate(params())
    assert r.current is None and r.promo is None and r.profitable is None
    assert UnitTax().title == "Налог не задан"
