"""Начисления из API: синхронизация на записанных ответах Ozon, пропуски, API, Celery."""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import settings
from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import OzonAPIError
from app.models.accrual import Accrual, AccrualDay, AccrualType
from app.models.api_credential import ApiCredential
from app.models.pnl_snapshot import PnLSnapshot
from app.models.product import Product
from app.models.sync_run import SUCCESS
from app.models.user import User
from app.services.encryption import encrypt
from app.services.sync import finance as fin
from app.services.sync import runner
from app.tasks import sync as tasks

FIXTURES = Path(__file__).parent / "fixtures" / "ozon"
API_KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"
DAY = date(2026, 10, 10)
P1, P2 = "0123456789-0001-1", "0123456789-0002-1"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeOzon:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.types = load("accrual_types")
        self.days = {(DAY.isoformat(), ""): load("accrual_by_day_p1"), (DAY.isoformat(), "p2"): load("accrual_by_day_p2")}
        self.postings = {p["posting_number"]: p for p in load("accrual_postings")["posting_accruals"]}
        self.calls: dict[str, list[dict]] = {"types": [], "day": [], "postings": []}
        self.fail_dates: set[str] = set()  # by-day за эти даты отвечает 400
        mock.post("/v1/finance/accrual/types").mock(side_effect=self._types)
        mock.post("/v1/finance/accrual/by-day").mock(side_effect=self._day)
        mock.post("/v1/finance/accrual/postings").mock(side_effect=self._postings)

    def _types(self, request: httpx.Request) -> httpx.Response:
        self.calls["types"].append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=self.types)

    def _day(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["day"].append(body)
        if body["date"] in self.fail_dates:
            return httpx.Response(400, json={"code": 3, "message": "invalid date"})
        return httpx.Response(200, json=self.days.get((body["date"], body["last_id"]), {"accruals": [], "last_id": ""}))

    def _postings(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["postings"].append(body)
        found = [self.postings[n] for n in body["posting_numbers"] if n in self.postings]
        return httpx.Response(200, json={"posting_accruals": found})


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeOzon(mock)


@pytest.fixture
def today(monkeypatch):
    """«Сегодня» по Москве — 11.10.2026; загрузка при подключении — 3 дня вместо 90."""
    monkeypatch.setattr(fin, "today_msk", lambda: date(2026, 10, 11))
    monkeypatch.setattr(fin, "BACKFILL_DAYS", 3)
    return date(2026, 10, 11)


async def make_user(sessions, email="seller@example.com", with_key=True) -> uuid.UUID:
    async with sessions() as db:
        user = User(email=email, hashed_password="x")
        db.add(user)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add_all([
            Product(user_id=user.id, ozon_product_id=1001, ozon_sku=900001001, offer_id="6001-2RS.C3.SRL.NAC.TH", synced_at=now),
            Product(user_id=user.id, ozon_product_id=1002, ozon_sku=900001002, offer_id="ACB305523.NAC.JP", synced_at=now),
        ])
        if with_key:
            db.add(ApiCredential(user_id=user.id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                                 is_active=True, expires_at=now + timedelta(days=60)))
        await db.commit()
        return user.id


async def sync(sessions, user_id, date_from=None, date_to=None) -> fin.FinanceSyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await fin.sync_finance(db, user_id, client, date_from, date_to)


async def accruals(sessions, user_id) -> list[Accrual]:
    async with sessions() as db:
        res = await db.execute(select(Accrual).where(Accrual.user_id == user_id).order_by(Accrual.sync_scope, Accrual.line_no))
        return list(res.scalars())


# ── синхронизация ──
async def test_sync_day(sessions, ozon):
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id, DAY, DAY)
    assert stats.as_dict() == {
        "days": 1, "entries": 4, "postings": 1, "postings_fallback": 1, "lines": 9, "types": 7,
        "date_from": "2026-10-10", "date_to": "2026-10-10",
    }
    # пагинация by-day по last_id с тем же date (требование документации)
    assert ozon.calls["day"] == [{"date": "2026-10-10", "last_id": ""}, {"date": "2026-10-10", "last_id": "p2"}]
    assert ozon.calls["postings"] == [{"posting_numbers": [P1, P2]}]

    rows = await accruals(sessions, user_id)
    by_scope = {}
    for r in rows:
        by_scope.setdefault(r.sync_scope, []).append(r)

    # отправление из /postings: строки с типом, SKU, количеством; товар привязан по SKU
    p1 = by_scope[f"posting:{P1}"]
    assert [(r.accrual_type_id, r.amount, r.accrual_date, r.quantity) for r in p1] == [
        (101, Decimal("540.00"), DAY, 1), (102, Decimal("-205.20"), DAY, 1),
        (103, Decimal("-63.00"), DAY, 1), (104, Decimal("27.70"), date(2026, 10, 11), 1),
    ]
    assert {r.posting_number for r in p1} == {P1} and all(r.product_id for r in p1)
    assert p1[0].raw_json["seller_price"]["amount"] == "540.00"  # сырой ответ сохранён

    # отправление, которого /postings не вернул, — разобрано из by-day и помечено
    fb = by_scope[f"posting-day:{P2}:2026-10-10"]
    assert [(r.accrual_type_id, r.amount) for r in fb] == [(101, Decimal("2150.00")), (999, Decimal("-450.00"))]
    assert fb[0].raw_json["fallback"] == "by-day" and fb[0].product_id is not None
    assert fb[0].raw_json["unit_number"] == P2  # исходная запись by-day сохранена для перепроверки

    # начисления без товара: подписка и хранение (container_fees — две строки)
    day = by_scope["day:2026-10-10"]
    assert sorted((r.accrual_type_id, r.amount) for r in day) == [
        (201, Decimal("-1990.00")), (202, Decimal("-8.40")), (202, Decimal("-4.00")),
    ]
    assert all(r.posting_number is None and r.product_id is None for r in day)
    assert {r.source for r in rows} == {"api"}


async def test_sync_marks_pnl_snapshots_stale(sessions, ozon):
    """Догруженные начисления за день — сохранённые итоги P&L, чей период его задевает, устарели (шаг 3.2)."""
    user_id = await make_user(sessions)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        for start, end in ((date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 31))):
            db.add(PnLSnapshot(user_id=user_id, date_from=start, date_to=end, source="auto", data_json={},
                               calculated_at=now))
        await db.commit()
    await sync(sessions, user_id, DAY, DAY)
    async with sessions() as db:
        snaps = {s.date_from.month: s for s in (await db.execute(select(PnLSnapshot))).scalars()}
    assert snaps[9].stale is False
    assert snaps[10].stale is True and snaps[10].stale_reason == "Начисления из Ozon API за 10.10.2026"


async def test_types_and_categories(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    async with sessions() as db:
        types = {t.id: t for t in (await db.execute(select(AccrualType))).scalars()}
    assert types[101].name_ru == "Выручка" and types[101].category == "income"
    assert types[102].category == "expense" and types[201].category == "expense"
    assert types[999].name_ru == "Начисление #999"  # нет в справочнике — заглушка
    assert types[105].category is None  # не встречался — категория неизвестна


async def test_resync_no_duplicates(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    first = len(await accruals(sessions, user_id))
    await sync(sessions, user_id, DAY, DAY)
    await sync(sessions, user_id, DAY, DAY)
    assert len(await accruals(sessions, user_id)) == first == 9


async def test_fallback_replaced_when_postings_returns_it(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    ozon.postings[P2] = {"posting_number": P2, "accruals": [
        {"accrual_date": "2026-10-10", "type_id": 101, "sku": 900001002, "quantity": 1, "accrued": {"amount": "2150.00"}},
        {"accrual_date": "2026-10-10", "type_id": 102, "sku": 900001002, "quantity": 1, "accrued": {"amount": "-817.00"}},
    ]}
    stats = await sync(sessions, user_id, DAY, DAY)
    assert stats.postings == 2 and stats.postings_fallback == 0
    scopes = {r.sync_scope for r in await accruals(sessions, user_id)}
    assert f"posting:{P2}" in scopes and not any(s.startswith("posting-day:") for s in scopes)


async def test_no_fallback_for_already_loaded_posting(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    del ozon.postings[P1]  # в следующий раз /postings его не вернул
    await sync(sessions, user_id, DAY, DAY)
    scopes = {r.sync_scope for r in await accruals(sessions, user_id)}
    assert f"posting:{P1}" in scopes and f"posting-day:{P1}:2026-10-10" not in scopes


async def test_postings_batches_of_200(sessions, ozon, monkeypatch):
    monkeypatch.setattr(fin, "POSTINGS_BATCH", 1)
    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    assert ozon.calls["postings"] == [{"posting_numbers": [P1]}, {"posting_numbers": [P2]}]


async def test_missing_periods_and_default_days(sessions, ozon, today):
    user_id = await make_user(sessions)
    async with sessions() as db:
        assert await fin.get_missing_periods(db, user_id, date(2026, 10, 1), date(2026, 10, 11)) == [
            (date(2026, 10, 1), date(2026, 10, 11)),
        ]
        # без периода: незагруженные дни за BACKFILL_DAYS (3) + вчера и сегодня
        assert await fin.days_to_sync(db, user_id) == [date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)]

    stats = await sync(sessions, user_id)
    assert stats.days == 3 and (stats.date_from, stats.date_to) == ("2026-10-09", "2026-10-11")
    async with sessions() as db:
        assert await fin.get_missing_periods(db, user_id, date(2026, 10, 1), date(2026, 10, 11)) == [
            (date(2026, 10, 1), date(2026, 10, 8)),
        ]
        # всё загружено — по расписанию снова только вчера и сегодня
        assert await fin.days_to_sync(db, user_id) == [date(2026, 10, 10), date(2026, 10, 11)]
        days = {d.day: d.entries for d in (await db.execute(select(AccrualDay))).scalars()}
    assert days == {date(2026, 10, 9): 0, date(2026, 10, 10): 4, date(2026, 10, 11): 0}  # пустой день тоже отмечен


async def test_gaps_in_middle(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id, date(2026, 10, 3), date(2026, 10, 4))
    await sync(sessions, user_id, date(2026, 10, 7), date(2026, 10, 7))
    async with sessions() as db:
        assert await fin.get_missing_periods(db, user_id, date(2026, 10, 1), date(2026, 10, 8)) == [
            (date(2026, 10, 1), date(2026, 10, 2)), (date(2026, 10, 5), date(2026, 10, 6)), (date(2026, 10, 8), date(2026, 10, 8)),
        ]


async def test_failure_midway_keeps_loaded_days(sessions, ozon):
    user_id = await make_user(sessions)
    ozon.fail_dates.add("2026-10-11")  # Ozon падает на втором дне
    with pytest.raises(OzonAPIError):
        await sync(sessions, user_id, DAY, date(2026, 10, 11))
    async with sessions() as db:
        # первый день сохранён, второй — в пропусках
        assert await fin.get_missing_periods(db, user_id, DAY, date(2026, 10, 11)) == [(date(2026, 10, 11), date(2026, 10, 11))]
    assert len(await accruals(sessions, user_id)) == 9


async def test_run_sync_finance_with_params(sessions, ozon):
    user_id = await make_user(sessions)
    run = await runner.run_sync("finance", user_id, sessions, params={"date_from": "2026-10-10", "date_to": "2026-10-10"})
    assert run.status == SUCCESS and run.kind == "finance" and run.stats["lines"] == 9


# ── API ──
def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def api_user(sessions, client, tokens, ozon) -> uuid.UUID:
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    user_id = uuid.UUID(me["id"])
    async with sessions() as db:
        now = datetime.now(timezone.utc)
        db.add_all([
            Product(user_id=user_id, ozon_product_id=1001, ozon_sku=900001001, offer_id="6001-2RS.C3.SRL.NAC.TH", synced_at=now),
            Product(user_id=user_id, ozon_product_id=1002, ozon_sku=900001002, offer_id="ACB305523.NAC.JP", synced_at=now),
            ApiCredential(user_id=user_id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                          is_active=True, expires_at=now + timedelta(days=60)),
        ])
        await db.commit()
    await sync(sessions, user_id, DAY, DAY)
    return user_id


async def test_api_requires_auth(client):
    for url in ("/api/v1/finance/accruals", "/api/v1/finance/accruals/by-day"):
        assert (await client.get(url)).status_code == 401
    assert (await client.post("/api/v1/finance/sync")).status_code == 401


async def test_api_accruals(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    get = lambda **q: client.get("/api/v1/finance/accruals", params={"date_from": "2026-10-01", "date_to": "2026-10-31", **q}, headers=auth(tokens))  # noqa: E731

    data = (await get()).json()
    assert data["data_source"] == "api" and "расходиться с личным кабинетом" in data["warning"] and "XLSX" in data["warning"]
    assert data["total"] == 9 and len(data["items"]) == 9
    # доход = 540 + 27.70 + 2150; расход = −205.20 − 63 − 450 − 1990 − 8.40 − 4
    assert data["totals"] == {"income": "2717.70", "expense": "-2720.60", "net": "-2.90"}
    first = data["items"][0]  # сортировка: новые даты сверху
    assert first["accrual_date"] == "2026-10-11" and first["type_name"] == "Баллы за скидки" and first["category"] == "income"
    assert first["product"]["offer_id"] == "6001-2RS.C3.SRL.NAC.TH" and first["posting_number"] == P1

    assert (await get(type_id=202)).json()["total"] == 2
    assert (await get(posting_number=P1)).json()["total"] == 4
    product_id = first["product"]["id"]
    assert (await get(product_id=product_id)).json()["total"] == 4
    page = (await get(limit=3, offset=3)).json()
    assert page["total"] == 9 and len(page["items"]) == 3 and page["totals"]["net"] == "-2.90"  # итоги — по всему фильтру


async def test_api_by_day(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    data = (await client.get("/api/v1/finance/accruals/by-day", params={"date_from": "2026-10-08", "date_to": "2026-10-11"},
                             headers=auth(tokens))).json()
    assert data["data_source"] == "api" and data["warning"]
    assert data["days"] == [
        {"date": "2026-10-10", "income": "2690.00", "expense": "-2720.60", "net": "-30.60", "lines": 8},
        {"date": "2026-10-11", "income": "27.70", "expense": "0.00", "net": "27.70", "lines": 1},
    ]
    assert data["totals"]["net"] == "-2.90"
    # загружен только 10.10 (11.10 пришло внутри отправления, но сам день не загружался)
    assert data["missing_periods"] == [
        {"date_from": "2026-10-08", "date_to": "2026-10-09"}, {"date_from": "2026-10-11", "date_to": "2026-10-11"},
    ]


@pytest.mark.parametrize("params,detail", [
    ({"date_from": "2026-10-10", "date_to": "2026-10-01"}, "Период: дата начала позже даты окончания"),
    ({"date_from": "2025-01-01", "date_to": "2026-10-01"}, "Период: не больше 366 дней за раз"),
    ({"date_from": "10.10.2026"}, "Дата начала: дата в формате ГГГГ-ММ-ДД"),
    ({"limit": 1000}, "Лимит: не больше 500"),
])
async def test_api_period_validation(client, tokens, params, detail):
    r = await client.get("/api/v1/finance/accruals", params=params, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"] == detail


async def test_api_isolation(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    t2 = (await client.post("/api/v1/auth/login", json=other)).json()
    data = (await client.get("/api/v1/finance/accruals", params={"date_from": "2026-10-01", "date_to": "2026-10-31"},
                             headers=auth(t2))).json()
    assert data["total"] == 0 and data["items"] == [] and data["data_source"] == "api"


async def test_api_sync(sessions, client, tokens, ozon, no_celery, today):
    await api_user(sessions, client, tokens, ozon)
    no_celery.clear()
    r = await client.post("/api/v1/finance/sync", headers=auth(tokens))
    assert r.status_code == 202 and r.json()["kind"] == "finance"
    assert no_celery[-1][0] == "finance" and no_celery[-1][3] is None  # без периода
    assert (await client.post("/api/v1/finance/sync", headers=auth(tokens))).status_code == 409  # уже в очереди
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["finance"]["last_run"]["status"] == "queued"


async def test_api_sync_with_period(sessions, client, tokens, ozon, no_celery, today):
    await api_user(sessions, client, tokens, ozon)
    r = await client.post("/api/v1/finance/sync", json={"date_from": "2026-09-01", "date_to": "2026-09-30"}, headers=auth(tokens))
    assert r.status_code == 202 and no_celery[-1][3] == {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@pytest.mark.parametrize("body,detail", [
    ({"date_from": "2026-09-01"}, "Запрос: укажите обе даты периода или ни одной"),
    ({"date_from": "2026-10-12", "date_to": "2026-10-12"}, "Период: дата окончания не может быть в будущем"),
    ({"date_from": "2021-12-30", "date_to": "2022-01-02"}, "Период: Ozon отдаёт начисления начиная с 01.01.2022"),
    ({"date_from": "2026-01-01", "date_to": "2026-06-01"}, "Период: не больше 92 дней за раз"),
])
async def test_api_sync_validation(sessions, client, tokens, ozon, today, body, detail):
    await api_user(sessions, client, tokens, ozon)
    r = await client.post("/api/v1/finance/sync", json=body, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"] == detail


async def test_api_sync_without_key(client, tokens):
    r = await client.post("/api/v1/finance/sync", headers=auth(tokens))
    assert r.status_code == 400 and "API-ключ" in r.json()["detail"]


async def test_backfill_queued_when_key_added(client, tokens, no_celery):
    with respx.mock(base_url=settings.OZON_API_URL) as mock:
        mock.post("/v1/roles").respond(200, json={"roles": []})
        mock.post("/v3/product/list").respond(200, json={"result": {"items": [], "total_items": 0, "last_id": ""}})
        r = await client.post("/api/v1/credentials", json={"client_id": "1234567", "api_key": API_KEY}, headers=auth(tokens))
    assert r.status_code == 201
    # 90 дней: начисления и баланс у Ozon (выплаты, шаг 3.6), без периода
    assert [q[0] for q in no_celery] == ["finance", "balance"] and no_celery[0][3] is None
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["finance"]["last_run"]["status"] == "queued"


async def test_key_saved_even_if_queue_down(client, tokens, monkeypatch):
    from app.api.v1 import sync as sync_api

    def down(*_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(sync_api, "enqueue_sync", down)
    with respx.mock(base_url=settings.OZON_API_URL) as mock:
        mock.post("/v1/roles").respond(200, json={"roles": []})
        mock.post("/v3/product/list").respond(200, json={"result": {"items": [], "total_items": 0, "last_id": ""}})
        r = await client.post("/api/v1/credentials", json={"client_id": "1234567", "api_key": API_KEY}, headers=auth(tokens))
    assert r.status_code == 201
    assert len((await client.get("/api/v1/credentials", headers=auth(tokens))).json()) == 1


# ── Celery ──
def test_beat_finance_every_6_hours():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sync-finance-every-6h"]
    assert entry["task"] == "tasks.sync_finance_all"
    assert entry["schedule"].hour == {0, 6, 12, 18} and entry["schedule"].minute == {23}
    assert tasks.FOR_USER_TASKS["finance"] is tasks.sync_finance_for_user


def test_finance_task_params(monkeypatch):
    seen = []

    async def fake(kind, user_id, run_id, params=None):
        seen.append((kind, run_id, params))
        return {"status": "success"}

    monkeypatch.setattr(tasks, "_sync_for_user", fake)
    tasks.sync_finance_for_user("u", "r")
    tasks.sync_finance_for_user("u", "r", "2026-09-01", "2026-09-30")
    assert seen == [("finance", "r", None), ("finance", "r", {"date_from": "2026-09-01", "date_to": "2026-09-30"})]


def test_finance_fanout(monkeypatch):
    users = [uuid.uuid4(), uuid.uuid4()]

    async def fake_users():
        return users

    queued = []
    monkeypatch.setattr(tasks, "_active_users", fake_users)
    monkeypatch.setattr(tasks.sync_finance_for_user, "delay", lambda uid: queued.append(uid))
    assert tasks.sync_finance_all() == 2 and queued == [str(u) for u in users]


async def test_db_unique_scope_line(sessions):
    """Даже в обход сервиса БД не даст записать одну строку области дважды."""
    from sqlalchemy.exc import IntegrityError

    user_id = await make_user(sessions)
    async with sessions() as db:
        db.add(AccrualType(id=1, name_ru="t"))
        row = dict(user_id=user_id, accrual_type_id=1, accrual_date=DAY, amount=Decimal("1"), source="api",
                   sync_scope="day:2026-10-10", line_no=0)
        db.add_all([Accrual(**row), Accrual(**row)])
        with pytest.raises(IntegrityError):
            await db.commit()


def test_fixtures_documented_format():
    assert "accrual_types" in load("accrual_types") and "posting_accruals" in load("accrual_postings")
    for name in ("accrual_by_day_p1", "accrual_by_day_p2"):
        data = load(name)
        assert set(data) == {"accruals", "last_id"} and all("accrual_id" in a and "unit_number" in a for a in data["accruals"])

