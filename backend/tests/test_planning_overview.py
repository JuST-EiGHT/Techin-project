"""Обзор «Планирования» и ДДС (шаг 3.10): ДДС на эталонном наборе (с переводами и возвратами), остаток на начало дня,
прогноз и разрыв в обзоре, факт слева и план справа, выплаты Ozon, пустые состояния."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.api.v1 import planning_overview as overview_api
from app.engine.cash_flow import Flow, buckets, cash_flow
from app.engine.payouts import monday
from tests.test_payouts import WEEKS, add_days, history
from tests.test_plan_calendar import TODAY, call, fixed_today  # noqa: F401 — фикстура «сегодня»
from app.services import planning_overview
from tests.test_pnl_api import user_id

D = Decimal
URL = "/api/v1/planning"


@pytest.fixture(autouse=True)
def overview_today(monkeypatch):
    for module in (planning_overview, overview_api):
        monkeypatch.setattr(module, "today_msk", lambda: TODAY)


# ---------- engine: ДДС ----------

SEPT_OCT = [
    Flow(date(2026, 9, 3), D(50000), "marketplace", "income"),
    Flow(date(2026, 9, 5), D(-30000), "rent", "expense"),
    Flow(date(2026, 9, 10), D(-5000), "transfer", "transfer", transfer=True),
    Flow(date(2026, 9, 10), D(5000), "transfer", "transfer", transfer=True),
    Flow(date(2026, 9, 15), D(-1200)),
    Flow(date(2026, 9, 20), D(2000)),
    Flow(date(2026, 9, 25), D(-500), "marketplace", "income"),  # исправление выплаты — уменьшает поступления
    Flow(date(2026, 10, 2), D(40000), "marketplace", "income"),
    Flow(date(2026, 10, 5), D(-30000), "rent", "expense"),
    Flow(date(2026, 10, 10), D(-12000), "salary", "expense"),
    Flow(date(2026, 10, 20), D(-999)),  # вне окна
]


def test_cash_flow_reference_by_month():
    r = cash_flow(D(100000), SEPT_OCT, date(2026, 9, 1), date(2026, 10, 14), "month")
    assert [(p.start, p.end, p.opening, p.income, p.expense, p.closing) for p in r.periods] == [
        (date(2026, 9, 1), date(2026, 9, 30), D("100000.00"), D("51500.00"), D("-31200.00"), D("120300.00")),
        (date(2026, 10, 1), date(2026, 10, 14), D("120300.00"), D("40000.00"), D("-42000.00"), D("118300.00")),
    ]
    assert (r.opening, r.income, r.expense, r.net, r.closing) == (
        D("100000.00"), D("91500.00"), D("-73200.00"), D("18300.00"), D("118300.00"))
    assert r.transfers == 2  # переводы — не приход и не расход
    assert [(row.section, row.category, row.values, row.total) for row in r.rows] == [
        ("income", "marketplace", [D("49500.00"), D("40000.00")], D("89500.00")),
        ("income", None, [D("2000.00"), D("0.00")], D("2000.00")),
        ("expense", "rent", [D("-30000.00"), D("-30000.00")], D("-60000.00")),
        ("expense", "salary", [D("0.00"), D("-12000.00")], D("-12000.00")),
        ("expense", None, [D("-1200.00"), D("0.00")], D("-1200.00")),
    ]
    # по статьям сходится с остатками
    assert r.opening + sum(row.total for row in r.rows) == r.closing


def test_cash_flow_by_week_and_unknown_opening():
    assert len(buckets(date(2026, 9, 1), date(2026, 10, 14), "week")) == 7  # вт 1.09 — первая неделя неполная
    r = cash_flow(None, SEPT_OCT, date(2026, 9, 1), date(2026, 10, 14), "week")
    first = r.periods[0]
    assert (first.start, first.end, first.income, first.expense) == (
        date(2026, 9, 1), date(2026, 9, 6), D("50000.00"), D("-30000.00"))
    assert r.periods[2].income == D("2000.00") and r.periods[2].expense == D("-1200.00")  # 14–20.09
    assert first.opening is None and r.closing is None  # остаток неизвестен — не 0
    assert r.income == D("91500.00")  # движение считается и без остатка
    empty = cash_flow(D(500), [], date(2026, 10, 1), date(2026, 10, 31))
    assert empty.rows == [] and empty.closing == D("500.00")


# ---------- API ----------


async def setup(client, tokens, balance="100000", supplier="upfront", **extra):
    accounts = [{"name": "Расчётный счёт", "kind": "checking", **({"balance": balance} if balance else {})}]
    await call(client, tokens, "POST", "/setup", json={
        "supplier_payment": supplier, "accounts": accounts, "ozon_account": 0, "categories": ["rent", "salary"], **extra})
    cats = {c["code"] or c["name"]: c["id"] for c in await call(client, tokens, "GET", "/categories")}
    accounts = {a["name"]: a["id"] for a in await call(client, tokens, "GET", "/accounts")}
    return cats, accounts


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


async def test_overview_forecast_gap_fact_and_plan(client, tokens):
    cats, accounts = await setup(client, tokens)
    acc = accounts["Расчётный счёт"]
    # факт до сегодня: остаток 100 000 указан на сегодня — раньше он считается назад по операциям
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "income", "amount": "15000", "day": iso(-7), "account_id": acc})
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "20000", "day": iso(-3), "account_id": acc, "category_id": cats["rent"]})
    await call(client, tokens, "POST", "/plans", 201, json={
        "kind": "expense", "title": "Аренда", "amount": "30000", "start_date": iso(2), "period": "month",
        "category_id": cats["rent"]})
    await call(client, tokens, "POST", "/plans", 201, json={
        "kind": "expense", "title": "Зарплата", "amount": "80000", "start_date": iso(5), "period": "month",
        "category_id": cats["salary"]})
    await call(client, tokens, "PUT", "/settings", json={"threshold": "20000"})

    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 28})
    assert (body["date_from"], body["date_to"]) == (iso(-14), iso(27))
    assert body["balance"] == "100000.00" and body["accounts"][0]["balance"] == "100000.00"
    assert body["first_below_zero"] == iso(5) and body["first_below_threshold"] == iso(5)
    assert body["min_balance"] == "-10000.00" and body["end_balance"] == "-10000.00"
    assert body["expected"] == {"ozon": "0.00", "income": "0.00", "expense": "-110000.00", "unknown": 0}
    assert body["next_ozon"] is None and body["ozon"] == {"enabled": True, "has_key": False}

    series = {d["day"]: d for d in body["series"]}
    assert len(series) == 42 and series[iso(-14)]["kind"] == "fact" and series[iso(0)]["kind"] == "plan"
    assert series[iso(-14)]["balance"] == "105000.00"  # 100 000 − (15 000 − 20 000)
    assert (series[iso(-7)]["income"], series[iso(-7)]["balance"]) == ("15000.00", "120000.00")
    assert (series[iso(-3)]["expense"], series[iso(-1)]["balance"]) == ("-20000.00", "100000.00")
    assert (series[iso(2)]["expense"], series[iso(2)]["balance"]) == ("-30000.00", "70000.00")
    assert series[iso(5)]["balance"] == "-10000.00"

    upcoming = [(u["title"], u["day"], u["status"]) for u in body["upcoming"]]
    assert upcoming == [("Аренда", iso(2), "planned"), ("Зарплата", iso(5), "planned")]
    assert body["empty"] == {"has_ops": True, "has_plans": True, "suggestions": []}

    # «оплачено» из обзора — факт сегодня, прогноз без двойного вычета
    await call(client, tokens, "POST", "/calendar/paid", json={"key": body["upcoming"][0]["key"]})
    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 28})
    today = {d["day"]: d for d in body["series"]}[iso(0)]
    assert today["fact"] == {"expense": "-30000.00", "ozon": "0.00", "income": "0.00"} and today["balance"] == "70000.00"
    assert body["upcoming"][0]["status"] == "paid" and body["first_below_zero"] == iso(5)


async def test_overview_series_any_period(client, tokens):
    """График за любой период: 5 дней назад и 5 вперёд, только прошлое, только будущее — остатки как в обзоре."""
    cats, accounts = await setup(client, tokens)
    acc = accounts["Расчётный счёт"]
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "income", "amount": "15000", "day": iso(-30), "account_id": acc})
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "20000", "day": iso(-3), "account_id": acc, "category_id": cats["rent"]})
    await call(client, tokens, "POST", "/plans", 201, json={
        "kind": "expense", "title": "Аренда", "amount": "30000", "start_date": iso(2), "period": "month",
        "category_id": cats["rent"]})
    await call(client, tokens, "PUT", "/settings", json={"threshold": "20000"})
    overview = {d["day"]: d for d in (await call(client, tokens, "GET", "/overview", params={"horizon_days": 28}))["series"]}

    # 5 дней назад и 5 вперёд: факт слева, план справа, остатки — те же, что в обзоре
    body = await call(client, tokens, "GET", "/overview/series", params={"date_from": iso(-5), "date_to": iso(5)})
    assert (body["today"], body["date_from"], body["date_to"], body["threshold"]) == (iso(0), iso(-5), iso(5), "20000.00")
    days = body["series"]
    assert [d["day"] for d in days] == [iso(n) for n in range(-5, 6)]
    assert [d["kind"] for d in days] == ["fact"] * 5 + ["plan"] * 6
    assert all(d == overview[d["day"]] for d in days)
    assert days[0]["balance"] == "120000.00" and days[2]["expense"] == "-20000.00"  # −3: аренда
    assert (days[7]["expense"], days[7]["balance"]) == ("-30000.00", "70000.00")  # +2: аренда по плану

    # только прошлое, дальше двух недель обзора: остаток назад по операциям, плана нет
    body = await call(client, tokens, "GET", "/overview/series", params={"date_from": iso(-40), "date_to": iso(-1)})
    days = {d["day"]: d for d in body["series"]}
    assert len(days) == 40 and all(d["kind"] == "fact" and d["fact"] is None for d in days.values())
    assert days[iso(-40)]["balance"] == "105000.00"  # 100 000 − (15 000 − 20 000)
    assert (days[iso(-30)]["income"], days[iso(-30)]["balance"]) == ("15000.00", "120000.00")
    assert days[iso(-1)]["balance"] == "100000.00"

    # только будущее, с завтрашнего дня: прогноз от сегодняшнего остатка
    body = await call(client, tokens, "GET", "/overview/series", params={"date_from": iso(1), "date_to": iso(40)})
    days = {d["day"]: d for d in body["series"]}
    assert len(days) == 40 and all(d["kind"] == "plan" for d in days.values())
    assert days[iso(1)]["balance"] == "100000.00" and days[iso(2)]["balance"] == "70000.00"
    nxt = (TODAY + timedelta(days=2)).replace(month=TODAY.month + 1).isoformat()  # аренда через месяц
    assert (days[nxt]["expense"], days[nxt]["balance"]) == ("-30000.00", "40000.00")



@pytest.mark.parametrize("params, text", [
    ({"date_from": "2026-10-10", "date_to": "2026-10-01"}, "дата начала позже"),
    ({"date_from": "2025-01-01", "date_to": "2026-10-01"}, "не больше года"),
    ({"date_from": "2026-10-14", "date_to": "2027-05-01"}, "не дальше 180 дней"),
])
async def test_overview_series_bad_period(client, tokens, params, text):
    await setup(client, tokens)
    r = await client.get(f"{URL}/overview/series", params=params,
                         headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 400 and text in r.json()["detail"]


async def test_overview_ozon_payouts_fact_and_forecast(client, tokens, sessions, monkeypatch):
    from app.services import ozon_payouts

    monkeypatch.setattr(ozon_payouts, "today_msk", lambda: TODAY)
    await setup(client, tokens, balance="50000")
    uid = await user_id(client, tokens)
    weeks = {w: v * 7 for w, v in WEEKS.items()}
    facts = {p.day: p.amount for p in history(weeks)}
    await add_days(sessions, uid, facts, {**weeks, monday(TODAY): D(7000)}, TODAY)
    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 30})
    nxt = body["next_ozon"]
    assert nxt["day"] == "2026-10-21" and nxt["amount"] == "25000.00" and nxt["title"].startswith("Выплата Ozon")
    series = {d["day"]: d for d in body["series"]}
    assert series["2026-10-21"]["ozon"] == "25000.00"  # план выплаты — своим цветом
    past = [d for d in body["series"] if d["kind"] == "fact" and d["ozon"] != "0.00"]
    assert past and all(d["day"] in {p.isoformat() for p in facts} for d in past)  # факт выплат — слева
    assert D(body["expected"]["ozon"]) > 0 and body["expected"]["expense"] == "0.00"
    # выключили выплаты Ozon в прогнозе — ближайшей выплаты нет
    await call(client, tokens, "PUT", "/settings", json={"gen_ozon": False})
    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 30})
    assert body["next_ozon"] is None and body["ozon"]["enabled"] is False


async def test_overview_empty_states(client, tokens):
    await setup(client, tokens, balance=None, supplier="own_production", categories=["materials"])
    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 30})
    assert body["balance"] is None and body["unknown_accounts"] == ["Расчётный счёт"]  # остаток не указан — не 0
    assert all(d["balance"] is None for d in body["series"]) and body["first_below_zero"] is None
    assert body["empty"]["has_ops"] is False and body["empty"]["has_plans"] is False
    codes = [s["code"] for s in body["empty"]["suggestions"]]
    assert codes == ["salary", "rent", "subscriptions", "loans", "bank", "materials", "production_salary", "workshop_rent"]
    assert next(s for s in body["empty"]["suggestions"] if s["code"] == "materials")["have"] is True
    assert any("нет данных о балансе Ozon" in w for w in body["warnings"])


async def test_cash_flow_api_with_transfers_and_opening(client, tokens):
    cats, accounts = await setup(client, tokens)
    acc = accounts["Расчётный счёт"]
    cash = (await call(client, tokens, "POST", "/accounts", 201, json={
        "name": "Наличные", "kind": "cash", "balance": "3000", "as_of": "2026-09-01"}))["id"]
    ops = [
        {"type": "income", "amount": "50000", "day": "2026-09-03", "account_id": acc, "category_id": cats["marketplace"]},
        {"type": "expense", "amount": "30000", "day": "2026-09-05", "account_id": acc, "category_id": cats["rent"]},
        {"type": "transfer", "amount": "5000", "day": "2026-09-10", "account_id": acc, "to_account_id": cash},
        {"type": "expense", "amount": "1200", "day": "2026-09-15", "account_id": cash},
        {"type": "expense", "amount": "12000", "day": "2026-10-10", "account_id": acc, "category_id": cats["salary"]},
    ]
    for op in ops:
        await call(client, tokens, "POST", "/operations", 201, json=op)
    body = await call(client, tokens, "GET", "/cash-flow",
                      params={"date_from": "2026-09-01", "date_to": "2026-10-14", "granularity": "month"})
    # расчётный: 100 000 на 14.10 → на 1.09 = 100 000 − (50 000 − 30 000 − 5 000 − 12 000) = 97 000; наличные 3 000
    assert (body["opening"], body["income"], body["expense"], body["closing"]) == (
        "100000.00", "50000.00", "-43200.00", "106800.00")
    assert [(p["income"], p["expense"], p["closing"]) for p in body["periods"]] == [
        ("50000.00", "-31200.00", "118800.00"), ("0.00", "-12000.00", "106800.00")]
    assert [(r["name"], r["total"]) for r in body["rows"]] == [
        ("Поступления от маркетплейсов", "50000.00"), ("Аренда", "-30000.00"), ("Зарплата", "-12000.00"),
        ("Без статьи", "-1200.00")]
    assert any(w.startswith("Переводов между своими счетами: 2") for w in body["warnings"])
    # по умолчанию — с начала месяца два месяца назад, по месяцам
    default = await call(client, tokens, "GET", "/cash-flow")
    assert (default["date_from"], default["date_to"], len(default["periods"])) == ("2026-08-01", "2026-10-14", 3)

    # движение по счёту без остатка — остатки по периодам не считаем
    card = (await call(client, tokens, "POST", "/accounts", 201, json={"name": "Карта", "kind": "card"}))["id"]
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "700", "day": "2026-10-01", "account_id": card})
    body = await call(client, tokens, "GET", "/cash-flow", params={"date_from": "2026-09-01", "date_to": "2026-10-14"})
    assert body["opening"] is None and body["closing"] is None and body["expense"] == "-43900.00"
    assert body["warnings"][0].startswith("Остаток не указан у счёта: Карта")


@pytest.mark.parametrize("params", [{"date_from": "2026-10-10", "date_to": "2026-10-01"},
                                    {"date_from": "2023-01-01", "date_to": "2026-10-01"}])
async def test_cash_flow_bad_period(client, tokens, params):
    await setup(client, tokens)
    r = await client.get(f"{URL}/cash-flow", params=params, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 400
