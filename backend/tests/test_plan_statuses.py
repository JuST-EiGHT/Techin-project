"""«Планирование» (шаг 10.24): статусы платежей календаря (запланирован, ожидается, оплачен, частично оплачен,
просрочен), остаток частично оплаченного в прогнозе, причины кассового разрыва, обзор (к подтверждению, план-факт,
баланс у Ozon) и ДДС с планом и прогнозным остатком, по одному счёту."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.api.v1 import planning_overview as overview_api
from app.engine import payment_calendar as cal
from app.engine.cash_flow import Flow, cash_flow
from app.services import planning_overview
from tests.test_payouts import add_days
from tests.test_plan_calendar import TODAY, call, calendar, fixed_today, setup  # noqa: F401 — фикстура «сегодня»
from tests.test_pnl_api import user_id

D = Decimal
d = date


@pytest.fixture(autouse=True)
def overview_today(monkeypatch):
    for module in (planning_overview, overview_api):
        monkeypatch.setattr(module, "today_msk", lambda: TODAY)


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


# ---------- engine: статусы и прогноз ----------


def item(key, days, amount, fact=None, kind="expense", **kw) -> cal.Item:
    it = cal.Item(key, kw.pop("source", "manual"), key, TODAY + timedelta(days=days), D(amount), kind, **kw)
    if fact is not None:
        it.fact = cal.Op(f"op-{key}", TODAY + timedelta(days=days), D(fact))
    return it


def test_statuses_reference():
    """Ожидается — срок сегодня или прошёл не больше чем на 3 дня; просрочен — больше; частично — факт меньше плана
    от 1 ₽ (у оценки и выплаты Ozon факт закрывает платёж целиком)."""
    items = {
        "future": item("future", 2, -1000),
        "today": item("today", 0, -1000),
        "late3": item("late3", -3, -1000),
        "late4": item("late4", -4, -1000),
        "paid": item("paid", -1, -1000, fact=-1000),
        "cents": item("cents", -1, -1000, fact="-999.50"),  # копейки — не частичная оплата
        "partial": item("partial", -1, -30000, fact=-28000),
        "partial_in": item("partial_in", -2, 5000, fact=3000, kind="income"),
        "estimate": item("estimate", -1, -1000, fact=-500, estimate=True),
        "ozon": item("ozon", 0, 9000, fact=8000, kind="income", source="ozon"),
        "skipped": item("skipped", -10, -1000, skipped=True),
        "off": item("off", 2, -1000, enabled=False),
    }
    assert {k: i.status(TODAY) for k, i in items.items()} == {
        "future": "planned", "today": "expected", "late3": "expected", "late4": "overdue", "paid": "paid",
        "cents": "paid", "partial": "partial", "partial_in": "partial", "estimate": "paid", "ozon": "paid",
        "skipped": "skipped", "off": "disabled",
    }
    assert (items["partial"].remaining, items["partial_in"].remaining) == (D("-2000.00"), D("2000.00"))
    assert items["cents"].remaining is None and items["paid"].remaining is None
    # в прогноз: запланированные — в свой день, «ожидается» — сегодня, остаток частичной оплаты — сегодня (до 3 дней)
    flows = {k: cal.forecast_flow(i, TODAY) for k, i in items.items()}
    assert flows["future"] == (TODAY + timedelta(days=2), D(-1000))
    assert flows["late3"] == (TODAY, D(-1000)) and flows["partial"] == (TODAY, D("-2000.00"))
    assert {k for k, f in flows.items() if f is None} == {"late4", "paid", "cents", "estimate", "ozon", "skipped", "off"}
    old_partial = item("old", -5, -1000, fact=-400)
    assert old_partial.status(TODAY) == "partial" and cal.forecast_flow(old_partial, TODAY) is None


def test_forecast_partial_and_gap_causes():
    items = [
        item("Аренда", 2, -30000),
        item("Зарплата", 6, -40000),
        item("Подрядчик", -1, -10000, fact=-6000),  # остаток 4 000 — сегодня
        item("Возврат долга", -9, 25000, kind="income"),  # просрочен — в прогноз не входит, это причина
        item("Поступление", -2, 5000, kind="income"),  # ожидается — сегодня
    ]
    fc = cal.forecast(D(50000), items, TODAY, TODAY + timedelta(days=10))
    by_day = {x.day: x.balance for x in fc.days}
    assert by_day[TODAY] == D("51000.00") and by_day[TODAY + timedelta(days=2)] == D("21000.00")
    assert fc.first_below_zero == TODAY + timedelta(days=6) and fc.min_balance == D("-19000.00")
    c = cal.gap_causes(items, TODAY, fc.first_below_zero)
    assert [(i.key, a) for i, a in c.expenses] == [
        ("Зарплата", D(-40000)), ("Аренда", D(-30000)), ("Подрядчик", D("-4000.00"))]
    assert [i.key for i in c.missing_income] == ["Возврат долга"]
    # платежи после дня разрыва — не причина
    assert [i.key for i, _ in cal.gap_causes(items, TODAY, TODAY + timedelta(days=2)).expenses] == ["Аренда", "Подрядчик"]


def test_cash_flow_with_forecast_and_plan():
    """Факт + оставшийся план = прогнозный остаток на конец; план периода — для план-факта, в остатки не входит."""
    r = cash_flow(
        D(1000), [Flow(d(2026, 10, 2), D(-200), "rent", "expense")], d(2026, 10, 1), d(2026, 11, 30), "month",
        forecast=[Flow(d(2026, 10, 20), D(-500), "rent", "expense"), Flow(d(2026, 11, 3), D(300))],
        planned=[Flow(d(2026, 10, 2), D(-250), "rent", "expense"), Flow(d(2026, 10, 20), D(-500), "rent", "expense")],
    )
    assert [(p.income, p.expense, p.forecast_income, p.forecast_expense, p.plan_expense, p.closing) for p in r.periods] == [
        (D("0.00"), D("-200.00"), D("0.00"), D("-500.00"), D("-750.00"), D("300.00")),
        (D("0.00"), D("0.00"), D("300.00"), D("0.00"), D("0.00"), D("600.00")),
    ]
    assert (r.closing, r.forecast_income, r.forecast_expense, r.plan_expense) == (
        D("600.00"), D("300.00"), D("-500.00"), D("-750.00"))
    rent = next(row for row in r.rows if row.category == "rent")
    assert (rent.values, rent.forecast, rent.plan) == (
        [D("-200.00"), D("0.00")], [D("-500.00"), D("0.00")], [D("-750.00"), D("0.00")])
    # по статьям сходится с прогнозным остатком
    assert r.opening + sum(row.total + row.forecast_total for row in r.rows) == r.closing


# ---------- API ----------


async def plan(client, tokens, title, kind, amount, day, category=None):
    body = {"kind": kind, "title": title, "amount": amount, "start_date": day,
            **({"category_id": category} if category else {})}
    return (await call(client, tokens, "POST", "/plans", 201, json=body))["plan_id"]


def key_of(body, title):
    return next(ln["key"] for ln in body["lines"] if ln["title"] == title)


async def test_calendar_statuses_partial_and_causes(client, tokens):
    cats, _ = await setup(client, tokens, balance="50000")
    await plan(client, tokens, "Аренда", "expense", "30000", iso(-2), cats["rent"])  # ожидается
    await plan(client, tokens, "Подрядчик", "expense", "10000", iso(-1))  # оплачен частично
    await plan(client, tokens, "Возврат долга", "income", "25000", iso(-6))  # просрочен
    await plan(client, tokens, "Зарплата", "expense", "40000", iso(3), cats["salary"])  # запланирован
    await plan(client, tokens, "Старый платёж", "expense", "1000", iso(-10))  # просрочен
    await plan(client, tokens, "Поставщик", "expense", "5000", iso(-4))  # оплачен
    body = await calendar(client, tokens, iso(-14), iso(20))
    await call(client, tokens, "POST", "/calendar/paid", json={"key": key_of(body, "Подрядчик"), "amount": "6000"})
    await call(client, tokens, "POST", "/calendar/paid", json={"key": key_of(body, "Поставщик")})

    body = await calendar(client, tokens, iso(-14), iso(20))
    got = {ln["title"]: (ln["status"], ln["remaining"]) for ln in body["lines"]}
    assert got == {
        "Аренда": ("expected", None), "Подрядчик": ("partial", "-4000.00"), "Возврат долга": ("overdue", None),
        "Зарплата": ("planned", None), "Старый платёж": ("overdue", None), "Поставщик": ("paid", None),
    }
    assert body["overdue"] == 2
    # прогноз: сегодня — аренда (ожидается) и остаток подрядчику; просроченные не входят
    days = {x["day"]: x for x in body["days"]}
    assert (days[iso(0)]["expense"], days[iso(0)]["balance"]) == ("-34000.00", "16000.00")
    assert days[iso(3)]["balance"] == "-24000.00" and body["first_below_zero"] == iso(3)
    c = body["causes"]
    assert (c["day"], c["below_zero"], c["min_balance"], c["shortfall"]) == (iso(3), True, "-24000.00", "24000.00")
    assert [(e["title"], e["amount"]) for e in c["expenses"]] == [
        ("Зарплата", "-40000.00"), ("Аренда", "-30000.00"), ("Подрядчик", "-4000.00")]
    assert [(m["title"], m["day"], m["amount"]) for m in c["missing_income"]] == [("Возврат долга", iso(-6), "25000.00")]

    # «Считать оплаченным полностью»: сумма платежа = факт — остатка нет
    await call(client, tokens, "POST", "/calendar/override", 204, json={"key": key_of(body, "Подрядчик"), "amount": "6000"})
    body = await calendar(client, tokens, iso(-14), iso(20))
    line = next(ln for ln in body["lines"] if ln["title"] == "Подрядчик")
    assert (line["status"], line["remaining"]) == ("paid", None)
    assert {x["day"]: x["balance"] for x in body["days"]}[iso(0)] == "20000.00"
    # перенос «ожидается» на завтра — снова запланирован
    await call(client, tokens, "POST", "/calendar/override", 204, json={"key": key_of(body, "Аренда"), "day": iso(1)})
    body = await calendar(client, tokens, iso(-14), iso(20))
    assert next(ln["status"] for ln in body["lines"] if ln["title"] == "Аренда") == "planned"

    # журнал «Операций» и план-факт правил знают «ожидается»
    journal = await call(client, tokens, "GET", "/journal", params={"date_from": iso(-14), "date_to": iso(20),
                                                                     "status": "overdue"})
    assert sorted(r["plan"]["title"] for r in journal["items"]) == ["Возврат долга", "Старый платёж"]
    assert (journal["counts"]["overdue"], journal["counts"]["expected"]) == (2, 0)  # аренду перенесли на завтра


async def test_overview_to_confirm_plan_fact_and_ozon_balance(client, tokens, sessions):
    cats, _ = await setup(client, tokens, balance="50000")
    await plan(client, tokens, "Аренда", "expense", "30000", iso(-2), cats["rent"])
    await plan(client, tokens, "Подрядчик", "expense", "10000", iso(-1))
    await plan(client, tokens, "Возврат долга", "income", "25000", iso(-6))
    await plan(client, tokens, "Зарплата", "expense", "40000", iso(3), cats["salary"])
    await plan(client, tokens, "Поставщик", "expense", "5000", iso(-4))
    body = await calendar(client, tokens, iso(-14), iso(20))
    await call(client, tokens, "POST", "/calendar/paid", json={"key": key_of(body, "Подрядчик"), "amount": "6000"})
    await call(client, tokens, "POST", "/calendar/paid", json={"key": key_of(body, "Поставщик")})

    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 28})
    # к подтверждению: просроченные → частично оплаченные → «ожидается»
    assert [(ln["title"], ln["status"]) for ln in body["to_confirm"]] == [
        ("Возврат долга", "overdue"), ("Подрядчик", "partial"), ("Аренда", "expected")]
    assert body["plan_fact"] == {"date_from": iso(-14), "paid": 1, "partial": 1, "expected": 1, "overdue": 1,
                                 "plan": "-20000.00", "fact": "-11000.00"}
    assert body["expected"]["expense"] == "-74000.00"  # аренда, остаток подрядчику, зарплата
    assert (body["causes"]["day"], body["causes"]["shortfall"]) == (iso(3), "24000.00")
    assert [m["title"] for m in body["causes"]["missing_income"]] == ["Возврат долга"]
    assert body["ozon_balance"] is None  # баланс Ozon не загружен — не 0

    uid = await user_id(client, tokens)
    await add_days(sessions, uid, {}, {TODAY - timedelta(days=9): D(7000)}, TODAY - timedelta(days=3))
    body = await call(client, tokens, "GET", "/overview", params={"horizon_days": 28})
    assert body["ozon_balance"] == {"amount": "25000.00", "day": iso(-3)}


async def test_cash_flow_with_plan_forecast_and_account(client, tokens):
    cats, accounts = await setup(client, tokens, balance="50000")
    acc = accounts["Расчётный счёт"]
    cash = (await call(client, tokens, "POST", "/accounts", 201, json={
        "name": "Наличные", "kind": "cash", "balance": "3000", "as_of": "2026-10-01"}))["id"]
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "1200", "day": "2026-10-02", "account_id": cash})
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "transfer", "amount": "5000", "day": "2026-10-10", "account_id": acc, "to_account_id": cash})
    await plan(client, tokens, "Аренда", "expense", "30000", "2026-10-05", cats["rent"])  # оплачена
    await plan(client, tokens, "Премия", "expense", "10000", "2026-10-13", cats["salary"])  # оплачена частично
    await plan(client, tokens, "Зарплата", "expense", "40000", "2026-10-20", cats["salary"])
    await plan(client, tokens, "Аренда ноябрь", "expense", "30000", "2026-11-05", cats["rent"])
    body = await calendar(client, tokens, "2026-10-01", "2026-11-30")
    await call(client, tokens, "POST", "/calendar/paid", json={"key": key_of(body, "Аренда")})
    await call(client, tokens, "POST", "/calendar/paid", json={"key": key_of(body, "Премия"), "amount": "6000"})

    period = {"date_from": "2026-10-01", "date_to": "2026-11-30", "granularity": "month"}
    body = await call(client, tokens, "GET", "/cash-flow", params=period)
    # расчётный: 50 000 сегодня → на 1.10 = 50 000 + 30 000 + 6 000 + 5 000 = 91 000; наличные 3 000
    assert (body["opening"], body["expense"], body["forecast_expense"], body["plan_expense"], body["closing"]) == (
        "94000.00", "-37200.00", "-74000.00", "-110000.00", "-17200.00")
    assert [(p["kind"], p["expense"], p["forecast_expense"], p["plan_expense"], p["closing"]) for p in body["periods"]] == [
        ("current", "-37200.00", "-44000.00", "-80000.00", "12800.00"),
        ("future", "0.00", "-30000.00", "-30000.00", "-17200.00")]
    rows = {r["name"]: (r["values"], r["forecast"], r["plan"]) for r in body["rows"]}
    assert [r["name"] for r in body["rows"]] == ["Аренда", "Зарплата", "Без статьи"]  # больше по модулю с планом — выше
    assert rows["Аренда"] == (["-30000.00", "0.00"], ["0.00", "-30000.00"], ["-30000.00", "-30000.00"])
    assert rows["Зарплата"] == (["-6000.00", "0.00"], ["-44000.00", "0.00"], ["-50000.00", "0.00"])
    assert body["has_plan"] is True and body["plan_note"] is None
    c = body["causes"]
    assert (c["day"], c["shortfall"]) == ("2026-11-05", "17200.00")
    assert [e["title"] for e in c["expenses"]] == ["Зарплата", "Аренда ноябрь", "Премия"]
    # остатки с прогнозом сходятся с календарём: остаток на 5.11 тот же
    days = {x["day"]: x["balance"] for x in (await calendar(client, tokens, iso(0), "2026-11-30"))["days"]}
    assert days["2026-11-05"] == "-17200.00"

    # один счёт: наличные — перевод с расчётного = приход этого счёта, плана по счёту нет
    one = await call(client, tokens, "GET", "/cash-flow", params={**period, "account_id": cash})
    assert (one["account_id"], one["opening"], one["income"], one["expense"], one["closing"]) == (
        cash, "3000.00", "5000.00", "-1200.00", "6800.00")
    assert [(r["name"], r["transfer"], r["total"]) for r in one["rows"]] == [
        ("Переводы между своими счетами", True, "5000.00"), ("Без статьи", False, "-1200.00")]
    assert one["has_plan"] is False and one["causes"] is None
    assert not any(w.startswith("Переводов между своими счетами") for w in one["warnings"])

    # длинный период — только факт, с пояснением
    long = await call(client, tokens, "GET", "/cash-flow", params={"date_from": "2025-09-01", "date_to": "2026-10-14"})
    assert long["plan_note"] == "План и прогноз — при периоде до 400 дней" and long["has_plan"] is False
