"""Синхронизация товаров и цен на записанных (фейковых) ответах Ozon — без сети."""
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.api.v1 import sync as sync_api
from app.core.config import settings
from app.integrations.ozon.client import OzonClient
from app.models.api_credential import ApiCredential
from app.models.product import Product, ProductPrice
from app.models.sync_run import FAILED, QUEUED, RUNNING, SUCCESS, SyncRun
from app.models.user import User
from app.services.encryption import encrypt
from app.services.sync import products as sync_mod
from app.services.sync import runner
from app.tasks import sync as tasks

FIXTURES = Path(__file__).parent / "fixtures" / "ozon"
API_KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeOzon:
    """Отвечает записанными ответами Ozon в зависимости от тела запроса."""

    def __init__(self, mock: respx.MockRouter) -> None:
        self.pages = {
            ("ALL", ""): load("product_list_all_p1"),
            ("ALL", "WzEwMDJd"): load("product_list_all_p2"),
            ("ARCHIVED", ""): load("product_list_archived"),
        }
        self.info = load("product_info_list")
        self.prices = load("product_prices")
        self.price_pages: dict[str, dict] | None = None  # cursor → ответ, для теста пагинации
        self.list_error: httpx.Response | None = None  # если задан — список товаров отвечает ошибкой
        self.calls: dict[str, list[dict]] = {"list": [], "info": [], "prices": []}
        mock.post("/v3/product/list").mock(side_effect=self._list)
        mock.post("/v3/product/info/list").mock(side_effect=self._info)
        mock.post("/v5/product/info/prices").mock(side_effect=self._prices)

    def _list(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["list"].append(body)
        if self.list_error is not None:
            return self.list_error
        page = self.pages.get((body["filter"]["visibility"], body["last_id"]))
        return httpx.Response(200, json=page or {"result": {"items": [], "total_items": 0, "last_id": ""}})

    def _info(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["info"].append(body)
        ids = {int(x) for x in body["product_id"]}  # Ozon принимает product_id строками int64
        return httpx.Response(200, json={"items": [i for i in self.info["items"] if i["id"] in ids]})

    def _prices(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["prices"].append(body)
        if self.price_pages is not None:
            return httpx.Response(200, json=self.price_pages[body["cursor"]])
        ids = {int(x) for x in body["filter"]["product_id"]}
        items = [i for i in self.prices["items"] if i["product_id"] in ids]
        return httpx.Response(200, json={"cursor": "", "total_items": len(items), "items": items})

    def item(self, kind: str, pid: int) -> dict:
        src = self.info["items"] if kind == "info" else self.prices["items"]
        return next(i for i in src if (i.get("id") or i.get("product_id")) == pid)


@pytest.fixture
def ozon(monkeypatch):
    monkeypatch.setattr(sync_mod, "LIST_LIMIT", 2)  # чтобы проверить пагинацию по last_id на 3 товарах
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeOzon(mock)


async def make_user(sessions, email="seller@example.com", with_key=True) -> uuid.UUID:
    async with sessions() as db:
        user = User(email=email, hashed_password="x")
        db.add(user)
        await db.flush()
        if with_key:
            db.add(ApiCredential(
                user_id=user.id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                is_active=True, expires_at=datetime.now(timezone.utc) + timedelta(days=100),
            ))
        await db.commit()
        return user.id


async def sync(sessions, user_id) -> sync_mod.ProductSyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_products(db, user_id, client)


async def products(sessions, user_id) -> dict[int, Product]:
    async with sessions() as db:
        res = await db.execute(select(Product).where(Product.user_id == user_id))
        return {p.ozon_product_id: p for p in res.scalars()}


async def snapshots(sessions, product_id) -> list[ProductPrice]:
    async with sessions() as db:
        res = await db.execute(select(ProductPrice).where(ProductPrice.product_id == product_id).order_by(ProductPrice.snapshot_at))
        return list(res.scalars())


# ── сервис синхронизации ──
async def test_first_sync_creates_products_and_prices(sessions, ozon):
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {"created": 4, "updated": 0, "unchanged": 0, "price_snapshots": 3}

    # пагинация: ALL — две страницы по last_id, затем ARCHIVED
    assert [(c["filter"]["visibility"], c["last_id"]) for c in ozon.calls["list"]] == [
        ("ALL", ""), ("ALL", "WzEwMDJd"), ("ARCHIVED", ""),
    ]
    assert ozon.calls["info"] == [{"product_id": ["1001", "1002", "1003", "1004"]}]  # строки, как в схеме Ozon

    ps = await products(sessions, user_id)
    p = ps[1001]
    assert (p.offer_id, p.name, p.ozon_sku, p.barcode, p.category_id) == (
        "6001-2RS.C3.SRL.NAC.TH", "Подшипник 6001-2RS C3 NACHI", 900001001, "4580143941234", 17028922,
    )
    assert p.vat == Decimal("0.22") and p.is_archived is False and p.synced_at is not None  # из "22%"
    assert ps[1002].vat == Decimal("0.22")  # из "0.22"
    assert ps[1003].barcode is None  # пустой список штрихкодов
    assert ps[1004].is_archived is True and ps[1004].ozon_sku == 900001004  # sku из списка товаров

    [snap] = await snapshots(sessions, p.id)
    assert (snap.price, snap.old_price, snap.min_price, snap.marketing_price) == (
        Decimal("540.00"), Decimal("720.00"), Decimal("420.00"), Decimal("512.30"),
    )
    assert (snap.commission_pct, snap.acquiring_pct, snap.auto_action_enabled, snap.price_index) == (
        Decimal("38.000"), Decimal("1.500"), False, "GREEN",
    )
    # цены строками (как в старых версиях API) разбираются так же
    [snap3] = await snapshots(sessions, ps[1003].id)
    assert snap3.price == Decimal("499.00") and snap3.commission_pct == Decimal("37.500")
    assert await snapshots(sessions, ps[1004].id) == []  # для архивного Ozon цен не прислал


async def test_resync_without_changes(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id)
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {"created": 0, "updated": 0, "unchanged": 4, "price_snapshots": 0}
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ProductPrice)) == 3


async def test_changes_counted_and_snapshot_only_on_price_change(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id)

    ozon.item("prices", 1001)["price"]["price"] = 560  # цена изменилась
    ozon.item("info", 1002)["name"] = "Подшипник ACB305523 NACHI (новое название)"  # карточка изменилась
    ozon.item("prices", 1003)["price"]["price"] = "499.0"  # то же число в другом виде — не изменение
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {"created": 0, "updated": 2, "unchanged": 2, "price_snapshots": 1}

    ps = await products(sessions, user_id)
    assert [s.price for s in await snapshots(sessions, ps[1001].id)] == [Decimal("540.00"), Decimal("560.00")]
    assert len(await snapshots(sessions, ps[1002].id)) == 1
    assert ps[1002].name.endswith("(новое название)")


async def test_batches(sessions, ozon, monkeypatch):
    monkeypatch.setattr(sync_mod, "BATCH_SIZE", 3)
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id)
    assert stats.created == 4
    assert [c["product_id"] for c in ozon.calls["info"]] == [["1001", "1002", "1003"], ["1004"]]
    assert [c["filter"]["product_id"] for c in ozon.calls["prices"]] == [["1001", "1002", "1003"], ["1004"]]
    assert all(c["limit"] == 3 for c in ozon.calls["prices"])


async def test_prices_cursor_pagination(sessions, ozon):
    items = ozon.prices["items"]
    ozon.price_pages = {
        "": {"cursor": "c2", "total_items": 3, "items": items[:2]},
        "c2": {"cursor": "", "total_items": 3, "items": items[2:]},
    }
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id)
    assert stats.price_snapshots == 3
    assert [c["cursor"] for c in ozon.calls["prices"]] == ["", "c2"]


async def test_missing_fields_tolerated(sessions, ozon):
    ozon.info["items"] = [i for i in ozon.info["items"] if i["id"] != 1002]  # нет карточки
    del ozon.item("prices", 1001)["price"]  # нет блока цен
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id)
    assert stats.created == 4
    p = (await products(sessions, user_id))[1002]
    assert p.offer_id == "ACB305523.NAC.JP" and p.name is None  # артикул — из списка товаров


async def test_users_isolated(sessions, ozon):
    a = await make_user(sessions, "a@example.com")
    b = await make_user(sessions, "b@example.com")
    await sync(sessions, a)
    assert (await sync(sessions, b)).created == 4
    assert len(await products(sessions, a)) == len(await products(sessions, b)) == 4


# ── запуски (runner) ──
async def test_run_sync_success(sessions, ozon):
    user_id = await make_user(sessions)
    run = await runner.run_sync("products", user_id, sessions)
    assert run.status == SUCCESS and run.stats["created"] == 4 and run.finished_at is not None


async def test_run_sync_ozon_error_recorded(sessions):
    user_id = await make_user(sessions)
    with respx.mock(base_url=settings.OZON_API_URL) as mock:
        mock.post("/v3/product/list").respond(401, json={"message": "Invalid Api-Key"})
        run = await runner.run_sync("products", user_id, sessions)
    assert run.status == FAILED and "Ozon не принял API-ключ" in run.error


async def test_run_sync_without_key(sessions):
    user_id = await make_user(sessions, with_key=False)
    run = await runner.run_sync("products", user_id, sessions)
    assert run.status == FAILED and "API-ключ" in run.error


async def test_run_sync_unexpected_error(sessions, ozon, monkeypatch):
    async def boom(*_):
        raise RuntimeError("внутренняя поломка")

    monkeypatch.setitem(runner.JOBS, "products", boom)
    user_id = await make_user(sessions)
    run = await runner.run_sync("products", user_id, sessions)
    assert run.status == FAILED and run.error == runner.INTERNAL_ERROR


async def test_run_skipped_when_already_running_and_stale_released(sessions, ozon):
    user_id = await make_user(sessions)
    async with sessions() as db:
        db.add(SyncRun(user_id=user_id, kind="products", status=RUNNING, started_at=datetime.now(timezone.utc)))
        await db.commit()
    assert await runner.run_sync("products", user_id, sessions) is None

    async with sessions() as db:  # воркер «упал» час назад
        run = (await db.execute(select(SyncRun))).scalar_one()
        run.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()
    run = await runner.run_sync("products", user_id, sessions)
    assert run.status == SUCCESS


# ── Celery ──
async def test_celery_task_wrapper(sessions, ozon, monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def test_sessions():
        yield sessions

    monkeypatch.setattr(tasks, "task_sessions", test_sessions)
    user_id = await make_user(sessions)
    result = await tasks._sync_for_user("products", str(user_id), None)
    assert result["status"] == SUCCESS and result["stats"]["created"] == 4


async def test_users_with_active_keys(sessions):
    ok = await make_user(sessions, "ok@example.com")
    await make_user(sessions, "nokey@example.com", with_key=False)
    inactive_user = await make_user(sessions, "blocked@example.com")
    expired = await make_user(sessions, "expired@example.com")
    async with sessions() as db:
        (await db.get(User, inactive_user)).is_active = False
        cred = (await db.execute(select(ApiCredential).where(ApiCredential.user_id == expired))).scalar_one()
        cred.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()
        assert await tasks.users_with_active_keys(db) == [ok]


def test_hourly_fanout_survives_one_failure(monkeypatch):
    users = [uuid.uuid4() for _ in range(3)]

    async def fake_users():
        return users

    queued = []

    def fake_delay(user_id):
        if user_id == str(users[0]):
            raise ConnectionError("broker down")
        queued.append(user_id)

    monkeypatch.setattr(tasks, "_active_users", fake_users)
    monkeypatch.setattr(tasks.sync_products_for_user, "delay", fake_delay)
    assert tasks.sync_products_all() == 2
    assert queued == [str(users[1]), str(users[2])]


def test_beat_schedule_hourly():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sync-products-hourly"]
    assert entry["task"] == "tasks.sync_products_all"
    assert entry["schedule"].hour == set(range(24)) and len(entry["schedule"].minute) == 1


# ── API ──
def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_api, "enqueue_sync", lambda kind, u, r, params=None: calls.append((u, r)) if kind == "products" else None)
    return calls


async def _add_key(sessions, client, tokens):
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    async with sessions() as db:
        db.add(ApiCredential(
            user_id=uuid.UUID(me["id"]), label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
            is_active=True, expires_at=datetime.now(timezone.utc) + timedelta(days=100),
        ))
        await db.commit()
    return uuid.UUID(me["id"])


async def test_api_requires_auth(client):
    assert (await client.post("/api/v1/sync/products")).status_code == 401
    assert (await client.get("/api/v1/sync/status")).status_code == 401


async def test_api_sync_without_key(client, tokens, enqueued):
    r = await client.post("/api/v1/sync/products", headers=auth(tokens))
    assert r.status_code == 400 and "API-ключ" in r.json()["detail"] and enqueued == []
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    empty = {"last_run": None, "last_success_at": None}
    assert status == {"has_active_key": False, "products": empty, "stocks": empty, "finance": empty, "promotions": empty}


async def test_api_manual_sync_flow(sessions, client, tokens, enqueued, ozon):
    user_id = await _add_key(sessions, client, tokens)
    r = await client.post("/api/v1/sync/products", headers=auth(tokens))
    assert r.status_code == 202 and r.json()["status"] == QUEUED
    run_id = uuid.UUID(r.json()["id"])
    assert enqueued == [(user_id, run_id)]

    # второй запуск, пока первый в очереди
    r = await client.post("/api/v1/sync/products", headers=auth(tokens))
    assert r.status_code == 409 and "уже идёт" in r.json()["detail"]

    # воркер выполняет поставленный API запуск
    run = await runner.run_sync("products", user_id, sessions, run_id)
    assert run.id == run_id and run.status == SUCCESS

    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["has_active_key"] is True
    assert status["products"]["last_run"]["status"] == SUCCESS
    assert status["products"]["last_run"]["stats"]["created"] == 4
    assert status["products"]["last_success_at"] is not None


async def test_api_broker_down(sessions, client, tokens, monkeypatch):
    await _add_key(sessions, client, tokens)

    def down(*_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(sync_api, "enqueue_sync", down)
    r = await client.post("/api/v1/sync/products", headers=auth(tokens))
    assert r.status_code == 503 and "недоступны" in r.json()["detail"]
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["products"]["last_run"]["status"] == FAILED  # запуск не висит в очереди
    # и следующий запуск не блокируется
    monkeypatch.setattr(sync_api, "enqueue_sync", lambda *_: None)
    assert (await client.post("/api/v1/sync/products", headers=auth(tokens))).status_code == 202


async def test_api_status_keeps_last_success_after_failure(sessions, client, tokens, ozon, enqueued):
    user_id = await _add_key(sessions, client, tokens)
    await runner.run_sync("products", user_id, sessions)
    ok_at = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()["products"]["last_success_at"]

    ozon.list_error = httpx.Response(403, json={"message": "Access denied"})
    await runner.run_sync("products", user_id, sessions)
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()["products"]
    assert status["last_run"]["status"] == FAILED and "нет прав" in status["last_run"]["error"]
    assert status["last_success_at"] == ok_at


def test_fixtures_use_total_items_not_total():
    for f in FIXTURES.glob("*.json"):
        assert '"total"' not in f.read_text(encoding="utf-8"), f.name


@pytest.mark.parametrize("raw,expected", [("20%", "0.2"), ("22%", "0.22"), ("0.1", "0.1"), (0.2, "0.2"), ("0", "0"), (None, None), ("", None), ("abc", None)])
def test_vat_formats(raw, expected):
    assert sync_mod._vat(raw) == (Decimal(expected) if expected is not None else None)


def test_fixture_copy_helper_isolated():
    # FakeOzon читает фикстуры заново — правки одного теста не протекают в другой
    a, b = load("product_prices"), load("product_prices")
    a["items"][0]["price"]["price"] = 1
    assert b["items"][0]["price"]["price"] == 540 and copy.deepcopy(b) == b
