"""Главная — дашборд Ozon (шаг 3.7): прошлый такой же период, цифры со сравнением, ряды, выплаты, пустые состояния."""
from datetime import date
from decimal import Decimal

from app.services import dashboard as dashboard_service
from app.services.pnl_cache import cache
from app.services import ozon_payouts as payouts_service
from app.services.dashboard import previous_period, revenue_series
from app.engine.payouts import monday
from tests.test_payouts import TODAY, WEEKS, add_days, history
from tests.test_pnl_api import add_api_day, pnl, set_profile, user_id
from tests.test_report_pnl import OWNER, WEEK, auth, import_reference_costs, upload


def test_previous_period():
    d = date
    cases = {
        (d(2026, 9, 1), d(2026, 9, 30)): (d(2026, 8, 1), d(2026, 8, 31)),  # месяц — прошлый месяц
        (d(2026, 3, 1), d(2026, 3, 31)): (d(2026, 2, 1), d(2026, 2, 28)),
        (d(2026, 1, 1), d(2026, 1, 31)): (d(2025, 12, 1), d(2025, 12, 31)),
        (d(2026, 7, 1), d(2026, 9, 30)): (d(2026, 4, 1), d(2026, 6, 30)),  # квартал — прошлый квартал
        (d(2026, 10, 1), d(2026, 10, 14)): (d(2026, 9, 1), d(2026, 9, 14)),  # месяц до сегодня — так же
        (d(2026, 3, 1), d(2026, 3, 30)): (d(2026, 2, 1), d(2026, 2, 28)),  # в феврале столько дней нет
        (d(2026, 9, 7), d(2026, 9, 13)): (d(2026, 8, 31), d(2026, 9, 6)),  # неделя — прошлая неделя
        (d(2026, 9, 10), d(2026, 9, 20)): (d(2026, 8, 30), d(2026, 9, 9)),
    }
    for period, expected in cases.items():
        assert previous_period(*period) == expected, period


def day(iso, revenue):
    return {"date": iso, "revenue": revenue}


def test_revenue_series_days_and_weeks():
    s = revenue_series([day("2026-09-07", "100"), day("2026-09-08", None)], [day("2026-08-31", "50")])
    assert s["granularity"] == "day"
    assert s["points"][1] == {"date": "2026-09-08", "date_to": "2026-09-08", "prev_date": None, "revenue": None,
                              "prev_revenue": None, "partial": True}  # нет данных — null, а не 0
    cur = [day(f"2026-07-{i:02d}" if i <= 31 else f"2026-08-{i - 31:02d}", "10" if i != 3 else None) for i in range(1, 47)]
    prev = [day("2026-05-01", "5")] * 46
    s = revenue_series(cur, prev)
    assert s["granularity"] == "week" and len(s["points"]) == 7  # 46 дней — по 7 дней
    first, last = s["points"][0], s["points"][-1]
    assert (first["revenue"], first["prev_revenue"], first["partial"]) == ("60", "35", True)
    assert (last["date"], last["date_to"], last["revenue"]) == ("2026-08-12", "2026-08-15", "40")


async def board(client, tokens, **params):
    r = await client.get("/api/v1/dashboard/ozon", params=params, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_empty_dashboard_shows_what_to_connect(client, tokens):
    body = await board(client, tokens, date_from="2026-09-07", date_to="2026-09-13")
    assert body["setup"] == {"has_key": False, "has_reports": False, "has_balance": False, "tax_set": False}
    assert body["data_source"] == "none" and body["prev_from"] == "2026-08-31"
    assert body["figures"]["revenue"] == {"value": None, "previous": None, "change_pct": None}  # нет данных ≠ 0
    assert body["figures"]["profit"]["kind"] == "before_tax"
    assert all(p["revenue"] is None for p in body["series"]["points"]) and len(body["series"]["points"]) == 7
    assert body["payouts"]["has_data"] is False and body["payouts"]["next_payout"] is None  # без баланса — не прогноз
    assert body["payouts"]["weeks"] == [] and body["payouts"]["upcoming"] == []
    bad = await client.get("/api/v1/dashboard/ozon", params={"date_from": "2026-09-13", "date_to": "2026-09-07"},
                           headers=auth(tokens))
    assert (bad.status_code, bad.json()["detail"]) == (400, "Период: дата начала позже даты окончания")


async def test_week_with_report_and_previous_week(client, tokens, sessions):
    await upload(client, tokens, WEEK)
    body = await board(client, tokens, date_from="2026-09-07", date_to="2026-09-13")
    assert body["setup"]["has_reports"] is True and body["data_source"] == "xlsx"
    rev = body["figures"]["revenue"]
    assert (rev["value"], rev["previous"], rev["change_pct"]) == ("23712.03", None, None)
    assert body["compare_note"] == "За прошлый период данных нет — сравнивать не с чем."
    assert body["figures"]["received"]["value"] == "10955.83"
    assert body["missing_cost_count"] > 0  # себестоимость не загружена — видно, а не 0
    points = body["series"]["points"]
    assert sum(Decimal(p["revenue"]) for p in points) == Decimal("23712.03")
    assert [p["prev_date"] for p in points][:2] == ["2026-08-31", "2026-09-01"]

    # прошлая неделя — один день из Ozon API: сравнение есть, но приблизительное
    uid = await user_id(client, tokens)
    await add_api_day(sessions, uid, date(2026, 9, 1), [("Выручка", "1000", 1, "A-1")])
    await cache.invalidate(uid)  # как после синхронизации начислений
    body = await board(client, tokens, date_from="2026-09-07", date_to="2026-09-13")
    assert body["prev_data_source"] == "api"
    assert (body["figures"]["revenue"]["previous"], body["figures"]["revenue"]["change_pct"]) == ("1000.00", "2271.2")
    assert body["compare_note"] == "В одном из периодов есть дни без данных — сравнение приблизительное."
    assert body["series"]["points"][1]["prev_revenue"] == "1000.00"
    assert body["series"]["points"][0]["prev_revenue"] is None


async def test_profit_after_tax_matches_pnl(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    body = await board(client, tokens, date_from="2026-09-07", date_to="2026-09-13")
    period = await pnl(client, tokens)
    profit, margin = body["figures"]["profit"], body["figures"]["margin"]
    assert (profit["kind"], profit["value"]) == ("net", "-1419.07")  # как в P&L периода
    assert margin["value"] == period["pnl"]["margin"] and margin["change_pp"] is None
    assert body["setup"]["tax_set"] is True and body["missing_cost_count"] == 0


async def test_payouts_block(client, tokens, sessions, monkeypatch):
    monkeypatch.setattr(payouts_service, "today_msk", lambda: TODAY)
    monkeypatch.setattr(dashboard_service, "today_msk", lambda: TODAY)
    uid = await user_id(client, tokens)
    weeks = {w: v * 7 for w, v in WEEKS.items()}
    await add_days(sessions, uid, {p.day: p.amount for p in history(weeks)}, {**weeks, monday(TODAY): Decimal(7000)}, TODAY)
    body = await board(client, tokens, date_from="2026-10-01", date_to="2026-10-14")
    pay = body["payouts"]
    assert body["setup"]["has_balance"] is True and pay["balance"] == "25000.00"
    assert pay["next_payout"]["date"] == "2026-10-21" and pay["next_payout"]["kind"] == "schedule"
    assert pay["schedule_title"] == "раз в неделю по средам, за неделю продаж 3 нед. назад"
    # ближайшие 14 дней — с 14.10 по 27.10: выплата 21.10, следующая (28.10) уже за окном; она же — «следующая выплата»
    assert [f["date"] for f in pay["upcoming"]] == ["2026-10-21"]
    assert pay["upcoming"][0] == pay["next_payout"]
    starts = [w["week_from"] for w in pay["weeks"]]
    assert starts[0] >= "2026-08-10" and starts[-1] <= "2026-11-09"  # 8 недель назад и 4 вперёд
    assert any(Decimal(w["paid"]) > 0 for w in pay["weeks"]) and any(Decimal(w["schedule"]) > 0 for w in pay["weeks"])
