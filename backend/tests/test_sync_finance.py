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
P1, P2, P3 = "0123456789-0001-1", "0123456789-0002-1", "0123456789-0003-1"
OFFER1, OFFER2 = "6001-2RS.C3.SRL.NAC.TH", "ACB305523.NAC.JP"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeOzon:
    """Ozon на записанных ответах реального формата (номера и суммы выдуманы). /v1/finance/accrual/postings сервис не
    вызывает — маршрута для него нет: любой запрос к нему уронит тест."""

    def __init__(self, mock: respx.MockRouter) -> None:
        self.types = load("accrual_types")
        self.days = {(DAY.isoformat(), ""): load("accrual_by_day_p1"), (DAY.isoformat(), "p2"): load("accrual_by_day_p2")}
        self.calls: dict[str, list[dict]] = {"types": [], "day": []}
        self.fail_dates: set[str] = set()  # by-day за эти даты отвечает 400
        mock.post("/v1/finance/accrual/types").mock(side_effect=self._types)
        mock.post("/v1/finance/accrual/by-day").mock(side_effect=self._day)

    def _types(self, request: httpx.Request) -> httpx.Response:
        self.calls["types"].append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=self.types)

    def _day(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["day"].append(body)
        if body["date"] in self.fail_dates:
            return httpx.Response(400, json={"code": 3, "message": "invalid date"})
        return httpx.Response(200, json=self.days.get((body["date"], body["last_id"]), {"accruals": [], "last_id": ""}))


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
        "days": 1, "entries": 7, "postings": 3, "unbalanced": 0, "lines": 18, "types": 16,
        "date_from": "2026-10-10", "date_to": "2026-10-10",
    }
    # пагинация by-day по last_id с тем же date (требование документации); /postings не вызывается
    assert ozon.calls["day"] == [{"date": "2026-10-10", "last_id": ""}, {"date": "2026-10-10", "last_id": "p2"}]

    rows = await accruals(sessions, user_id)
    assert {r.sync_scope for r in rows} == {"day:2026-10-10"} and {r.source for r in rows} == {"api"}
    lines = [(r.posting_number, r.accrual_type_id, r.amount, r.quantity) for r in rows]
    assert lines == [
        # продажа 1 шт.: выручка = sale_amount − bonus − coinvestment, баллы, партнёры, комиссия, доставка
        (P1, fin.REVENUE, Decimal("537.50"), 1), (P1, fin.BONUS, Decimal("450.20"), 1),
        (P1, fin.PARTNERS, Decimal("12.30"), 1), (P1, fin.COMMISSION, Decimal("-520.00"), 1),
        (P1, 32, Decimal("-80.00"), 1), (P1, 29, Decimal("-9.50"), 1),
        # 2 шт.: суммы — за строку целиком; нулевых программ партнёров нет
        (P2, fin.REVENUE, Decimal("2000.00"), 2), (P2, fin.BONUS, Decimal("1000.00"), 2),
        (P2, fin.COMMISSION, Decimal("-1560.00"), 2), (P2, 32, Decimal("-150.00"), 2), (P2, 29, Decimal("-20.00"), 2),
        # ITEM: эквайринг и «Звёздные товары» по товару; NON_ITEM: размещение
        (P1, 1, Decimal("-10.75"), None), (P1, 74, Decimal("-27.50"), None),
        ("1234567890123", 46, Decimal("-120.40"), None),
        # стр. 2: доставка без продажи (невыкуп), вывоз со склада по номеру поставки, кросс-докинг по грузоместам
        (P3, 32, Decimal("-85.00"), 1), ("RU12-AB123456789", 71, Decimal("-250.00"), None),
        ("12345678", 12, Decimal("-30.00"), None), ("12345678", 12, Decimal("-12.00"), None),
    ]
    # товар привязан по SKU, у начислений без товара — нет; исходная запись Ozon сохранена
    by_line = {(r.posting_number, r.accrual_type_id): r for r in rows}
    assert by_line[(P1, fin.REVENUE)].product_id and by_line[(P1, 1)].product_id
    assert by_line[("1234567890123", 46)].product_id is None
    assert by_line[(P1, fin.REVENUE)].raw_json["unit_number"] == P1
    # сумма строк дня = сумма total_amount записей Ozon
    assert sum(r.amount for r in rows) == Decimal("1124.85")


def test_parse_entry_like_xlsx_report():
    """Сверено с отчётом XLSX владельца: Выручка = sale_amount − bonus − coinvestment, Баллы за скидки = bonus,
    Программы партнёров = coinvestment, Вознаграждение за продажу = commission; сумма строк = total_amount."""
    entry = load("accrual_by_day_p1")["accruals"][0]
    lines = fin.parse_entry(entry, DAY)
    amounts = {ln["type_id"]: ln["amount"] for ln in lines}
    assert amounts[fin.REVENUE] == Decimal("537.50")  # = sale_price при 1 шт.
    assert amounts[fin.REVENUE] + amounts[fin.BONUS] + amounts[fin.PARTNERS] == Decimal("1000.00")  # = seller_price
    assert fin.is_balanced(entry, lines)
    assert {ln["date"] for ln in lines} == {DAY} and {ln["posting"] for ln in lines} == {P1}


def test_parse_return_and_unknown_formats():
    ret = {"accrued_category": "POSTING", "date": "2026-10-12", "unit_number": P1,
           "total_amount": {"amount": "-480"},
           "posting": {"products": [{"sku": 900001001, "quantity": 1, "commission": {
               "sale_amount": {"amount": "-1000"}, "bonus": {"amount": "-450.2"}, "coinvestment": {"amount": "-12.3"},
               "commission": {"amount": "520"}}, "delivery": {"services": []}}]}}
    lines = fin.parse_entry(ret, DAY)
    # отрицательная выручка — «Возврат выручки» (по реальному возврату ещё не сверено — шаг 8.4a)
    assert [(ln["type_id"], ln["amount"]) for ln in lines] == [
        (fin.RETURN, Decimal("-537.50")), (fin.BONUS, Decimal("-450.20")), (fin.PARTNERS, Decimal("-12.30")),
        (fin.COMMISSION, Decimal("520.00")),
    ]
    assert lines[0]["date"] == date(2026, 10, 12)
    # разобрать нечего — итог записи по accrual_id; сумма не сходится — запись помечается
    bare = {"accrual_id": 46, "date": "2026-10-10", "total_amount": {"amount": "-5"}}
    assert [(ln["type_id"], ln["amount"]) for ln in fin.parse_entry(bare, DAY)] == [(46, Decimal("-5.00"))]
    odd = {"total_amount": {"amount": "-100"}, "non_item_fee": {"type_id": 46, "accrued": {"amount": "-60"}}}
    assert not fin.is_balanced(odd, fin.parse_entry(odd, DAY))


async def test_unbalanced_entries_counted(sessions, ozon):
    user_id = await make_user(sessions)
    ozon.days[(DAY.isoformat(), "p2")]["accruals"][1]["total_amount"]["amount"] = "-999"
    stats = await sync(sessions, user_id, DAY, DAY)
    assert stats.unbalanced == 1 and stats.lines == 18  # строки всё равно сохранены — как их разобрал Ozon


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
    # русское название — из description справочника, английский код — в code
    assert (types[69].code, types[69].name_ru) == ("SaleCommission", "Вознаграждение за продажу")
    assert (types[32].name_ru, types[32].category) == ("Логистика", "expense")
    # служебные типы сумм продажи — с названиями как в отчёте XLSX и категорией
    assert {t: (types[t].name_ru, types[t].category) for t in fin.VIRTUAL_TYPES} == {
        fin.REVENUE: ("Выручка", "income"), fin.RETURN: ("Возврат выручки", "expense"),
        fin.BONUS: ("Баллы за скидки", "income"), fin.PARTNERS: ("Программы партнёров", "income"),
        fin.COMMISSION: ("Вознаграждение за продажу", "expense"),
    }
    assert types[84].category is None  # не встречался — категория неизвестна


async def test_unknown_type_gets_stub(sessions, ozon):
    user_id = await make_user(sessions)
    ozon.days[(DAY.isoformat(), "p2")]["accruals"][2]["container_fees"]["fees"][0]["type_id"] = 999
    await sync(sessions, user_id, DAY, DAY)
    async with sessions() as db:
        stub = await db.get(AccrualType, 999)
    assert stub.name_ru == "Начисление #999" and stub.category == "expense"


async def test_resync_no_duplicates(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    first = len(await accruals(sessions, user_id))
    await sync(sessions, user_id, DAY, DAY)
    await sync(sessions, user_id, DAY, DAY)
    assert len(await accruals(sessions, user_id)) == first == 18


async def test_api_lines_feed_pnl_sales_and_dashboard(sessions, ozon):
    """Главная и P&L по данным API: выручка из записей отправлений, продажи по дням — по штукам (жалоба владельца
    20.10.2026: на главной не было продаж за месяц)."""
    from app.engine.product_pnl import group_of
    from app.models.sales_daily import SalesDaily
    from app.services import dashboard, pnl_service

    user_id = await make_user(sessions)
    await sync(sessions, user_id, DAY, DAY)
    async with sessions() as db:
        pnl = await pnl_service.get_pnl(db, user_id, pnl_service.PnLRequest(DAY, DAY))
        sales = {r.offer_id: (r.qty_sold, r.revenue) for r in (await db.execute(select(SalesDaily))).scalars()}
        home = await dashboard.ozon_dashboard(db, user_id, date(2026, 10, 1), DAY)
    assert pnl["data_source"] == "api" and Decimal(pnl["revenue_base"]) == Decimal("2537.50")
    assert {k: Decimal(v) for k, v in pnl["by_type"].items() if k in (
        "Выручка", "Баллы за скидки", "Программы партнёров", "Вознаграждение за продажу", "Логистика")} == {
        "Выручка": Decimal("2537.50"), "Баллы за скидки": Decimal("1450.20"), "Программы партнёров": Decimal("12.30"),
        "Вознаграждение за продажу": Decimal("-2080.00"), "Логистика": Decimal("-315.00"),
    }
    assert sales == {OFFER1: (1, Decimal("537.50")), OFFER2: (2, Decimal("2000.00"))}
    assert Decimal(home["figures"]["revenue"]["value"]) == Decimal("2537.50")
    # группы прибыли по товарам — по русским названиям справочника
    assert [group_of(n) for n in ("Вознаграждение за продажу", "Логистика", "Доставка до места выдачи", "Эквайринг")] == [
        "commission", "logistics", "logistics", "other",
    ]


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
    assert days == {date(2026, 10, 9): 0, date(2026, 10, 10): 7, date(2026, 10, 11): 0}  # пустой день тоже отмечен


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
    assert len(await accruals(sessions, user_id)) == 18


async def test_run_sync_finance_with_params(sessions, ozon):
    user_id = await make_user(sessions)
    run = await runner.run_sync("finance", user_id, sessions, params={"date_from": "2026-10-10", "date_to": "2026-10-10"})
    assert run.status == SUCCESS and run.kind == "finance" and run.stats["lines"] == 18


async def test_failed_run_keeps_ozon_text(sessions, ozon):
    """Общий отказ Ozon (HTTP 400) — в журнале запусков и на сайте видно метод и текст Ozon, а не только код."""
    from app.integrations.ozon.errors import OzonAuthError

    user_id = await make_user(sessions)
    ozon.fail_dates.add("2026-10-10")
    run = await runner.run_sync("finance", user_id, sessions, params={"date_from": "2026-10-10", "date_to": "2026-10-10"})
    assert run.status == "failed"
    assert run.error == "Ozon отклонил запрос (HTTP 400) — /v1/finance/accrual/by-day: invalid date"
    # ошибки ключа уже объясняют причину — без технических подробностей
    auth_error = OzonAuthError("Ozon не принял API-ключ", ozon_message="Invalid Api-Key", path="/v1/roles")
    assert runner.ozon_error_text(auth_error) == "Ozon не принял API-ключ"


async def test_task_sessions_close_pnl_cache_client():
    """Клиент Redis кэша P&L закрывается в цикле своей задачи Celery — без «Event loop is closed» в журнале."""
    import asyncio

    from app.services.pnl_cache import cache

    class FakeRedis:
        closed = False

        async def aclose(self):
            self.closed = True

    fake = FakeRedis()
    cache._client, cache._loop = fake, asyncio.get_running_loop()
    async with tasks.task_sessions():
        pass
    assert fake.closed and cache._client is None


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
    assert data["total"] == 18 and len(data["items"]) == 18
    # доход: выручка, баллы, партнёры; расход: комиссия, доставка, эквайринг, размещение, вывоз, кросс-докинг
    assert data["totals"] == {"income": "4000.00", "expense": "-2875.15", "net": "1124.85"}
    first = data["items"][0]
    assert first["accrual_date"] == "2026-10-10" and first["type_name"] == "Выручка" and first["category"] == "income"
    assert first["product"]["offer_id"] == OFFER1 and first["posting_number"] == P1 and first["quantity"] == 1

    assert (await get(type_id=32)).json()["total"] == 3
    assert (await get(posting_number=P1)).json()["total"] == 8
    product_id = first["product"]["id"]
    assert (await get(product_id=product_id)).json()["total"] == 9
    page = (await get(limit=3, offset=3)).json()
    assert page["total"] == 18 and len(page["items"]) == 3 and page["totals"]["net"] == "1124.85"  # итоги — по всему фильтру


async def test_api_by_day(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    data = (await client.get("/api/v1/finance/accruals/by-day", params={"date_from": "2026-10-08", "date_to": "2026-10-11"},
                             headers=auth(tokens))).json()
    assert data["data_source"] == "api" and data["warning"]
    assert data["days"] == [{"date": "2026-10-10", "income": "4000.00", "expense": "-2875.15", "net": "1124.85", "lines": 18}]
    assert data["totals"]["net"] == "1124.85"
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
    assert [q[0] for q in no_celery] == ["finance", "balance", "postings"] and no_celery[0][3] is None
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
    """Фикстуры — формат ответов Ozon с реального магазина (20.10.2026), номера и суммы выдуманы."""
    types = load("accrual_types")["accrual_types"]
    assert all({"id", "name", "description"} == set(t) for t in types)
    for name in ("accrual_by_day_p1", "accrual_by_day_p2"):
        data = load(name)
        assert set(data) == {"accruals", "last_id"}
        for a in data["accruals"]:
            assert {"accrual_id", "accrued_category", "date", "unit_number", "total_amount", "posting", "item_fees",
                    "non_item_fee", "container_fees"} == set(a)
