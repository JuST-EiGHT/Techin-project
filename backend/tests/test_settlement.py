"""«Выплаты и расчёты с Ozon» (шаг 10.23): сверка каждой выплаты с поступлением в банк (связанные загрузкой, «другой
суммой» вторым проходом, не пришло, ждём выписку, без выплаты, без выписок) и расхождения с объяснением; API за период —
баланс, начисления по статьям, сверка, расхождения со ссылкой на операцию банка."""
from datetime import date
from decimal import Decimal

import pytest

from app.api.v1 import bank as bank_api
from app.engine import settlement as eng
from tests.test_bank import OUR, OZON_ACC, OZON_INN, doc, send, setup, statement
from tests.test_payouts import add_days
from tests.test_plan_calendar import (  # noqa: F401 — фикстура «сегодня»
    TODAY,
    call,
    fixed_today,
)
from tests.test_pnl_api import user_id
from tests.test_report_pnl import WEEK, auth, upload

D = Decimal
d = date


@pytest.fixture(autouse=True)
def bank_today(monkeypatch):
    monkeypatch.setattr(bank_api, "today_msk", lambda: TODAY)


# ---------- engine: эталон сверки ----------

PAYOUTS = [
    eng.Payout(d(2026, 9, 30), D(50000), D(49000), d(2026, 9, 8)),  # +2 % к графику — не расхождение
    eng.Payout(d(2026, 10, 1), D(7000)),
    eng.Payout(d(2026, 10, 7), D(40000), D(50000), d(2026, 9, 15)),  # −20 % к графику
    eng.Payout(d(2026, 10, 12), D(12000)),
    eng.Payout(d(2026, 10, 14), D(20000)),
]
RECEIPTS = [
    eng.Receipt("r1", d(2026, 10, 2), D(50000), payout_day=d(2026, 9, 30)),  # связала загрузка выписки
    eng.Receipt("r2", d(2026, 10, 8), D("39800"), payout_day=d(2026, 10, 7)),  # связала, на 200 меньше
    eng.Receipt("r3", d(2026, 10, 13), D(9000)),  # не связана (разница > 1 %) — «другой суммой» к 12.10
    eng.Receipt("r4", d(2026, 9, 20), D(3000)),  # без выплаты
]


def test_reconcile_reference():
    rows = eng.reconcile(PAYOUTS, RECEIPTS, bank_until=d(2026, 10, 14), has_bank=True)
    got = [(r.day.isoformat(), r.status, r.receipt.id if r.receipt else None, r.diff, r.linked) for r in rows]
    assert got == [
        ("2026-10-14", "waiting", None, None, False),  # выписка до 14.10 — а деньги могут прийти до 19.10
        ("2026-10-12", "diff", "r3", D("-3000.00"), False),
        ("2026-10-07", "diff", "r2", D("-200.00"), True),
        ("2026-10-01", "no_bank", None, None, False),  # выписка за 01–06.10 есть, поступления нет
        ("2026-09-30", "ok", "r1", D("0.00"), True),
        ("2026-09-20", "bank_only", "r4", None, False),
    ]
    # выплата раньше первой выписки — «нет выписки», а не «не пришло»
    early = eng.reconcile(PAYOUTS, RECEIPTS, bank_until=d(2026, 10, 14), has_bank=True, bank_from=d(2026, 10, 2))
    assert next(r for r in early if r.payout and r.payout.day == d(2026, 10, 1)).status == "waiting"
    # выписок нет вовсе — сверки нет; поступление ближе по сумме выигрывает
    assert {r.status for r in eng.reconcile(PAYOUTS, [], None, has_bank=False)} == {"no_statements"}
    two = eng.reconcile([eng.Payout(d(2026, 10, 7), D(1000))],
                        [eng.Receipt("a", d(2026, 10, 7), D(500)), eng.Receipt("b", d(2026, 10, 9), D(990))],
                        d(2026, 10, 14), True)
    assert [(r.status, r.receipt.id) for r in two] == [("diff", "b"), ("bank_only", "a")]


def test_issues_reference():
    rows = eng.reconcile(PAYOUTS, RECEIPTS, bank_until=d(2026, 10, 14), has_bank=True)
    found = eng.issues(rows, {d(2026, 10, 16): D(15000)}, D(10), reliable=True,
                       mismatches=[{"period": "01.10.2026–15.10.2026", "ours": "109000", "ozon": "110000"}])
    assert [(i.kind, i.severity, i.day.isoformat(), i.amount) for i in found] == [
        ("overdue", "red", "2026-10-16", D(15000)),
        ("bank_missing", "red", "2026-10-01", D(7000)),
        ("ozon_report", "yellow", "2026-10-15", None),
        ("bank_diff", "yellow", "2026-10-12", D(3000)),
        ("bank_diff", "yellow", "2026-10-07", D(200)),
        ("schedule_diff", "yellow", "2026-10-07", D(10000)),  # ожидали 50 000, выплачено 40 000
        ("bank_only", "yellow", "2026-09-20", None),
    ]
    texts = {i.kind: i for i in found}
    assert texts["bank_missing"].title == "Выплата 01.10.2026 не пришла на счёт"
    assert "в выписке за 01.10.2026–06.10.2026 поступления от Ozon нет" in texts["bank_missing"].text
    assert texts["schedule_diff"].title == "Выплата 07.10.2026 меньше ожидаемой на 20.0 %"
    assert "за продажи 15.09.2026–21.09.2026 ожидали 50 000,00 ₽" in texts["schedule_diff"].text
    assert next(i for i in found if i.day == d(2026, 10, 7) and i.kind == "bank_diff").title == "На счёт пришло на 200,00 ₽ меньше"
    # график ненадёжен — с ним не сверяем; копейки округления — не расхождение
    assert "schedule_diff" not in {i.kind for i in eng.issues(rows, {}, D(10), reliable=False)}
    cents = eng.reconcile([eng.Payout(d(2026, 10, 7), D(1000))], [eng.Receipt("a", d(2026, 10, 8), D("999.50"))],
                          d(2026, 10, 14), True)
    assert cents[0].status == "diff" and eng.issues(cents, {}, D(10), True) == []


# ---------- API ----------


async def test_settlement_api(client, tokens, sessions):
    await upload(client, tokens, WEEK)  # начисления 07–13.09
    url = "/api/v1/ozon/settlement"
    period = {"date_from": "2026-09-07", "date_to": "2026-10-14"}
    empty = (await client.get(url, params=period, headers=auth(tokens))).json()
    assert (empty["has_balance"], empty["balance"], empty["payouts"], empty["has_bank"]) == (False, None, [], False)
    assert empty["accruals"]["total"] == "10955.83" and empty["accruals"]["missing_days"] == 31  # пропуски — не 0
    assert [r["key"] for r in empty["accruals"]["rows"]][:3] == ["revenue", "returns", "adjustments"]

    accounts = await setup(client, tokens, balance="100000")
    uid = await user_id(client, tokens)
    await add_days(sessions, uid, {d(2026, 9, 30): D(50000), d(2026, 10, 7): D(40000), d(2026, 10, 12): D(12000)},
                   {d(2026, 9, 28): D(7000), d(2026, 10, 5): D(7000), d(2026, 10, 12): D(7000)}, TODAY)
    body = (await client.get(url, params={"date_from": "2026-09-28", "date_to": "2026-10-14"}, headers=auth(tokens))).json()
    assert body["balance"] == {"opening": "1.00", "opening_day": "2026-09-28", "accrued": "17000.00", "paid": "102000.00",
                               "closing": "25000.00", "closing_day": "2026-10-14", "days_total": 17, "missing_days": 0}
    assert [(p["payout_day"], p["status"]) for p in body["payouts"]] == [
        ("2026-10-12", "no_statements"), ("2026-10-07", "no_statements"), ("2026-09-30", "no_statements")]
    assert body["issues"] == [] and body["totals"] == {"paid": "102000.00", "received": None, "count": 3}

    # выписка: 50 000 через 2 дня — та же сумма, 39 800 — на 200 меньше (связаны загрузкой), 9 000 — без пары (> 1 %)
    rows = [doc(201, "02.10.2026", "50000.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"'),
            doc(202, "08.10.2026", "39800.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"'),
            doc(203, "13.10.2026", "9000.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"')]
    await send(client, tokens, statement(rows), dry_run=False, account_id=accounts["Расчётный счёт"])
    body = (await client.get(url, params={"date_from": "2026-09-28", "date_to": "2026-10-14"}, headers=auth(tokens))).json()
    assert [(p["payout_day"], p["status"], p["bank_day"], p["diff"]) for p in body["payouts"]] == [
        ("2026-10-12", "diff", "2026-10-13", "-3000.00"), ("2026-10-07", "diff", "2026-10-08", "-200.00"),
        ("2026-09-30", "ok", "2026-10-02", "0.00")]
    assert body["counts"] == {"diff": 2, "ok": 1} and body["totals"]["received"] == "98800.00"
    issues = [(i["kind"], i["day"], i["amount"]) for i in body["issues"]]
    assert issues == [("bank_diff", "2026-10-12", "3000.00"), ("bank_diff", "2026-10-07", "200.00")]
    first = body["issues"][0]
    assert first["account_id"] == accounts["Расчётный счёт"] and first["op_id"] and first["bank_day"] == "2026-10-13"
    # та же сверка — у «Счетов» (3.11) и аномалий 7.1
    check = await call(client, tokens, "GET", "/ozon-check", base="/api/v1/bank",
                       params={"date_from": "2026-09-28", "date_to": "2026-10-14"})
    assert [(r["payout_day"], r["status"]) for r in check["rows"]] == [
        ("2026-10-12", "diff"), ("2026-10-07", "diff"), ("2026-09-30", "ok")]

    bad = await client.get(url, params={"date_from": "2026-10-14", "date_to": "2026-10-01"}, headers=auth(tokens))
    assert bad.status_code == 400
