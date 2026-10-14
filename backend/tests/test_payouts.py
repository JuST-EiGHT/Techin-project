"""Выплаты Ozon (шаг 3.6): рабочий календарь, график по выплатам, прогноз, синхронизация баланса, API."""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import settings
from app.engine.payouts import Payout, Schedule, detect_schedule, expected, forecast, intended_day, monday, overdue
from app.engine.workdays import is_workday, next_workday
from app.integrations.ozon.client import OzonClient
from app.models.ozon_balance import OzonBalanceDay
from app.services import ozon_payouts as payouts_service
from app.services.sync import ozon_balance as sync_mod
from tests.test_sync_finance import API_KEY, auth, make_user

TODAY = date(2026, 10, 14)  # среда; 4 ноября — праздник


def test_work_calendar():
    for day in (date(2026, 1, 9), date(2026, 3, 9), date(2026, 5, 11), date(2026, 11, 4), date(2026, 12, 31)):
        assert not is_workday(day), day  # переносы 2026 и праздники
    assert is_workday(date(2026, 3, 10)) and not is_workday(date(2026, 10, 17))
    assert next_workday(date(2026, 11, 4)) == date(2026, 11, 5)
    assert next_workday(date(2026, 12, 31)) == date(2027, 1, 11)  # 2027: праздники без переносов — оценка
    assert intended_day(date(2026, 11, 5)) == date(2026, 11, 4)  # перенос с праздника — день графика
    assert intended_day(date(2026, 10, 19)) == date(2026, 10, 19)  # понедельник — не «с воскресенья»


def history(weeks: dict, lag=3, weekday=2, keep=Decimal(1), until=TODAY):
    """Выплаты раз в неделю в weekday за неделю продаж lag недель назад (× keep — после комиссии)."""
    out = []
    for i in range(0, 14):
        base = monday(until) - timedelta(weeks=i) + timedelta(days=weekday)
        acc = weeks.get(monday(base) - timedelta(weeks=lag))
        if base <= until and acc:
            out.append(Payout(next_workday(base), (acc * keep).quantize(Decimal("0.01"))))
    return out


WEEKS = {monday(TODAY) - timedelta(weeks=i): Decimal(10000 + 500 * i) for i in range(1, 20)}


def test_weekly_wednesday_lag_3_and_forecast():
    s = detect_schedule(history(WEEKS), WEEKS, TODAY)
    assert (s.mode, s.weekday, s.lag_weeks, s.fee_pct, s.confidence) == ("weekly", 2, 3, 0, "high")
    assert s.title == "раз в неделю по средам, за неделю продаж 3 нед. назад"
    plan = forecast(s, TODAY, WEEKS, paid_today=True, balance=Decimal(15000))
    first, second, third = plan[:3]
    assert (first.day, first.kind, first.week_from) == (date(2026, 10, 21), "schedule", date(2026, 9, 28))
    assert first.amount == Decimal("11000.00")
    assert second.amount == Decimal("4000.00")  # не больше, чем осталось на балансе у Ozon
    assert (third.day, third.kind) == (date(2026, 11, 5), "estimate")  # 4 ноября — праздник; неделя продаж идёт
    assert third.amount == Decimal("11250.00")  # среднее за 4 полные недели
    assert expected(s, date(2026, 10, 14), WEEKS) == (date(2026, 9, 21), Decimal("11500.00"))


def test_weekly_with_flexible_fee():
    s = detect_schedule(history(WEEKS, lag=1, weekday=0, keep=Decimal("0.975")), WEEKS, TODAY)
    assert (s.weekday, s.lag_weeks, s.fee_pct, s.confidence) == (0, 1, Decimal("2.5"), "high")
    s.fee_pct = Decimal("1.50")  # из настройки — с двумя знаками
    assert s.title == "раз в неделю по понедельникам, за неделю продаж 1 нед. назад, комиссия 1,5 %"


def test_daily_irregular_and_little_history():
    days = [TODAY - timedelta(days=i) for i in range(1, 40) if is_workday(TODAY - timedelta(days=i))]
    daily = detect_schedule([Payout(d, Decimal(1000)) for d in days], WEEKS, TODAY)
    assert daily.mode == "daily" and daily.title == "каждый рабочий день"
    plan = forecast(daily, TODAY, WEEKS, paid_today=False, balance=None, horizon_weeks=1)
    assert all(is_workday(f.day) and f.kind == "estimate" for f in plan) and len(plan) == 6
    odd = [date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 18), date(2026, 9, 24), date(2026, 10, 6)]
    irregular = detect_schedule([Payout(d, Decimal(1000)) for d in odd], WEEKS, TODAY)
    assert irregular.mode == "irregular" and forecast(irregular, TODAY, WEEKS, False, None) == []
    assert detect_schedule([Payout(date(2026, 10, 7), Decimal(1))] * 2, WEEKS, TODAY) is None


def test_overdue_only_on_loaded_days():
    s = Schedule("weekly", 2, 3)
    loaded = {TODAY - timedelta(days=i) for i in range(1, 20)}
    late = overdue(s, TODAY, {date(2026, 9, 30)}, loaded)
    assert late == [date(2026, 10, 7)]  # 7 октября выплаты не было, 30 сентября — была
    assert overdue(s, TODAY, set(), set()) == []  # нет данных — не просрочка


def balance_resp(day: date, payment: Decimal = Decimal(0), accrued: Decimal = Decimal("1000")) -> dict:
    money = lambda v: {"currency_code": "RUB", "value": float(v)}  # noqa: E731
    return {
        "cashflows": {"sales": {"amount": money(accrued + 200)}, "returns": {"amount": money(-50)},
                      "services": [{"name": "Логистика", "amount": money(-150)}]},
        "total": {"opening_balance": money(5000), "accrued": money(accrued), "closing_balance": money(6000),
                  "payments": [money(-payment)] if payment else []},
    }


class FakeBalance:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.payments: dict[str, Decimal] = {}
        self.halves: dict[str, Decimal] = {}
        self.calls: list[dict] = []
        mock.post("/v1/finance/balance").mock(side_effect=self._balance)
        mock.post("/v1/finance/cash-flow-statement/list").mock(side_effect=self._cash_flow)

    def _balance(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        assert body["date_from"] == body["date_to"]  # по одному дню
        day = date.fromisoformat(body["date_from"])
        return httpx.Response(200, json=balance_resp(day, self.payments.get(body["date_from"], Decimal(0))))

    def _cash_flow(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        start = body["date"]["from"][:10]
        if start not in self.halves:
            return httpx.Response(200, json={"result": {"cash_flows": [], "page_count": 0}})
        return httpx.Response(200, json={"result": {"cash_flows": [], "details": {
            "payments": [{"payment": -float(self.halves[start]), "currency_code": "RUB"}]}, "page_count": 1}})


@pytest.fixture
def fake_balance():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeBalance(mock)


async def run_sync(sessions, user_id, date_from, date_to):
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_balance(db, user_id, client, date_from, date_to)


async def test_sync_days_snapshots_and_reconcile(sessions, fake_balance, monkeypatch):
    monkeypatch.setattr(sync_mod, "today_msk", lambda: TODAY)
    uid = await make_user(sessions)
    fake_balance.payments = {"2026-09-02": Decimal("12000.50"), "2026-09-09": Decimal("8000")}
    fake_balance.halves = {"2026-09-01": Decimal("20000.50")}  # Ozon: за 1–15 сентября на 1 рубль меньше
    stats = await run_sync(sessions, uid, date(2026, 9, 1), date(2026, 9, 15))
    assert (stats.days, stats.changed, stats.payouts, stats.checked_halves) == (15, 15, 2, 1)
    assert stats.mismatches == []  # 12 000,50 + 8 000 = как у Ozon
    async with sessions() as db:
        row = (await db.execute(select(OzonBalanceDay).where(OzonBalanceDay.day == date(2026, 9, 2)))).scalar_one()
    assert (row.payments, row.accrued, row.closing, row.sales, row.services) == (
        Decimal("-12000.50"), Decimal("1000.00"), Decimal("6000.00"), Decimal("1200.00"), Decimal("-150.00")
    )
    # повтор без изменений — без новых снимков; изменилась выплата — новый снимок дня
    fake_balance.payments["2026-09-09"] = Decimal("8100")
    fake_balance.halves["2026-09-01"] = Decimal("20000")
    stats = await run_sync(sessions, uid, date(2026, 9, 1), date(2026, 9, 15))
    assert stats.changed == 1
    assert stats.mismatches == [{"period": "01.09.2026–15.09.2026", "ozon": "20000.00", "ours": "20100.50"}]
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OzonBalanceDay)) == 16


async def test_default_days_and_reconcile_failure(sessions, fake_balance, monkeypatch):
    monkeypatch.setattr(sync_mod, "today_msk", lambda: TODAY)
    monkeypatch.setattr(sync_mod, "BACKFILL_DAYS", 5)
    uid = await make_user(sessions)
    stats = await run_sync(sessions, uid, None, None)
    assert [c["date_from"] for c in fake_balance.calls] == [f"2026-10-{d}" for d in (10, 11, 12, 13, 14)]
    assert stats.reconcile_error is None
    fake_balance.calls.clear()
    await run_sync(sessions, uid, None, None)
    assert [c["date_from"] for c in fake_balance.calls] == ["2026-10-13", "2026-10-14"]  # вчера и сегодня


async def add_days(sessions, uid, payouts: dict[date, Decimal], accrued_by_week: dict[date, Decimal], until: date):
    """Баланс по дням: начислено недели поровну на 7 дней, выплаты — в свои дни."""
    async with sessions() as db:
        for week, total in accrued_by_week.items():
            for i in range(7):
                d = week + timedelta(days=i)
                if d > until:
                    continue
                db.add(OzonBalanceDay(user_id=uid, day=d, accrued=total / 7, payments=-payouts.get(d, Decimal(0)),
                                      opening=Decimal(1), closing=Decimal("25000"), synced_at=datetime.now(timezone.utc)))
        await db.commit()


async def test_payouts_api(client, tokens, sessions, monkeypatch):
    monkeypatch.setattr(payouts_service, "today_msk", lambda: TODAY)
    empty = (await client.get("/api/v1/ozon/payouts", headers=auth(tokens))).json()
    assert empty["has_data"] is False and empty["schedule"]["source"] == "standard"
    assert empty["schedule"]["title"] == "раз в неделю по средам, за неделю продаж 3 нед. назад"
    assert any(w.startswith("Выплат в истории пока мало") for w in empty["warnings"])

    uid = uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])
    weeks = {w: v * 7 for w, v in WEEKS.items()}  # делится на 7 без остатка
    facts = {p.day: p.amount for p in history(weeks)}
    # текущая неделя загружена не целиком (пн–ср) — в начисления по неделям не входит
    await add_days(sessions, uid, facts, {**weeks, monday(TODAY): Decimal(7000)}, TODAY)
    body = (await client.get("/api/v1/ozon/payouts", headers=auth(tokens))).json()
    assert body["schedule"]["source"] == "auto" and body["schedule"]["confidence"] == "high"
    assert body["balance"] == "25000.00" and body["last_day"] == "2026-10-14"
    assert body["next_payout"] == {"date": "2026-10-21", "amount": "25000.00", "kind": "schedule",
                                   "week_from": "2026-09-28", "week_to": "2026-10-04"}  # 77 000, но на балансе 25 000
    assert body["history"][0]["date"] == "2026-10-14" and body["history"][0]["diff"] == "0.00"
    assert body["overdue"] == [] and body["weeks"][-1]["estimate"] != "0.00"

    # свой график: по четвергам — лаг берётся по выплатам; неверные данные — понятная ошибка
    bad = await client.put("/api/v1/ozon/payout-settings", json={"mode": "weekly"}, headers=auth(tokens))
    assert (bad.status_code, bad.json()["detail"]) == (400, "Укажите день недели, в который приходят выплаты")
    r = await client.put("/api/v1/ozon/payout-settings", json={"mode": "weekly", "weekday": 3}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["created"] is True and r.json()["current"]["weekday"] == 3
    again = await client.put("/api/v1/ozon/payout-settings", json={"mode": "weekly", "weekday": 3}, headers=auth(tokens))
    assert again.json()["created"] is False and len(again.json()["history"]) == 1
    body = (await client.get("/api/v1/ozon/payouts", headers=auth(tokens))).json()
    assert body["schedule"]["source"] == "user" and body["schedule"]["title"].startswith("раз в неделю по четвергам")
    assert body["next_payout"]["date"] == "2026-10-15"
    custom = {"mode": "custom", "weekday": 4, "lag_weeks": 2, "fee_pct": "1.5"}
    body = (await client.put("/api/v1/ozon/payout-settings", json=custom, headers=auth(tokens))).json()
    assert body["current"]["fee_pct"] == "1.50" and len(body["history"]) == 2
    r = await client.put("/api/v1/ozon/payout-settings", json={"mode": "auto", "fee_pct": "11"}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Комиссия за гибкий график: не больше 10")


async def test_balance_days_and_sync_endpoints(client, tokens, sessions):
    uid = uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])
    await add_days(sessions, uid, {date(2026, 9, 9): Decimal(500)}, {date(2026, 9, 7): Decimal(700)}, date(2026, 9, 13))
    days = (await client.get("/api/v1/ozon/balance", params={"date_from": "2026-09-06", "date_to": "2026-09-09"},
                             headers=auth(tokens))).json()
    assert days[0]["loaded"] is False and days[0]["accrued"] is None  # не загружен — null, а не 0
    assert (days[3]["payments"], days[3]["accrued"]) == ("-500.00", "100.00")
    r = await client.post("/api/v1/sync/balance", headers=auth(tokens))
    assert r.status_code in (202, 400)  # без ключа Ozon — понятная ошибка, с ключом — в очередь
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert "balance" in status
    other = await make_user(sessions, email="other@example.com", with_key=False)
    assert other != uid
    login = {"email": "x2@example.com", "password": "strongpass1"}
    await client.post("/api/v1/auth/register", json=login)
    t2 = (await client.post("/api/v1/auth/login", json=login)).json()
    assert (await client.get("/api/v1/ozon/payouts", headers=auth(t2))).json()["has_data"] is False
