"""Склады и остатки: расчёт статуса, синхронизация на записанных ответах Ozon, API, Celery."""
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
from app.engine.stocks import days_of_supply, stock_status
from app.integrations.ozon.client import OzonClient
from app.models.api_credential import ApiCredential
from app.models.product import Product
from app.models.sync_run import SUCCESS
from app.models.user import User
from app.models.warehouse import ProductStock, Warehouse
from app.services import stocks as stocks_service
from app.services.encryption import encrypt
from app.services.sync import runner
from app.services.sync import stocks as sync_mod
from app.tasks import sync as tasks

FIXTURES = Path(__file__).parent / "fixtures" / "ozon"
API_KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"
EKB, SPB, KRD = 1020000000000101, 1020000000000202, 1020000000000303
FBS_WH, RFBS_WH = 20605650762000, 20605650763000


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ── расчёт: дни запаса и статус ──
@pytest.mark.parametrize(
    "available,avg,days,status",
    [
        (10, None, None, None),  # продаж в БД нет — не 0, а «нет данных»
        (0, None, None, None),
        (10, Decimal("0"), None, "excess"),  # продаж нет, а товар лежит
        (0, Decimal("0"), None, None),
        (0, Decimal("1"), Decimal("0.0"), "critical"),
        (69, Decimal("10"), Decimal("6.9"), "critical"),
        (70, Decimal("10"), Decimal("7.0"), "low"),
        (139, Decimal("10"), Decimal("13.9"), "low"),
        (140, Decimal("10"), Decimal("14.0"), "normal"),
        (600, Decimal("10"), Decimal("60.0"), "normal"),
        (601, Decimal("10"), Decimal("60.1"), "excess"),
        (10, Decimal("0.25"), Decimal("40.0"), "normal"),
    ],
)
def test_days_of_supply_and_status(available, avg, days, status):
    assert days_of_supply(available, avg) == days
    assert stock_status(available, avg) == status


# ── фейковый Ozon ──
class FakeOzon:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.fbo = load("analytics_stocks")
        self.warehouses = load("warehouse_list")
        self.fbs_stocks = {FBS_WH: load("warehouse_stocks"), RFBS_WH: {"stocks": [], "has_next": False, "cursor": ""}}
        self.warehouse_pages: dict[str, dict] | None = None
        self.stock_pages: dict[str, dict] | None = None
        self.calls: dict[str, list[dict]] = {"fbo": [], "warehouses": [], "fbs": []}
        mock.post("/v1/analytics/stocks").mock(side_effect=self._fbo)
        mock.post("/v2/warehouse/list").mock(side_effect=self._warehouses)
        mock.post("/v1/product/info/warehouse/stocks").mock(side_effect=self._fbs)

    def _fbo(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["fbo"].append(body)
        skus = {int(s) for s in body["skus"]}
        return httpx.Response(200, json={"items": [i for i in self.fbo["items"] if i["sku"] in skus]})

    def _warehouses(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["warehouses"].append(body)
        if self.warehouse_pages is not None:
            return httpx.Response(200, json=self.warehouse_pages[body.get("cursor", "")])
        return httpx.Response(200, json=self.warehouses)

    def _fbs(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["fbs"].append(body)
        if self.stock_pages is not None and body["warehouse_id"] == FBS_WH:
            return httpx.Response(200, json=self.stock_pages[body["cursor"]])
        return httpx.Response(200, json=self.fbs_stocks[body["warehouse_id"]])

    def fbo_row(self, sku: int, wid: int) -> dict:
        return next(i for i in self.fbo["items"] if i["sku"] == sku and i["warehouse_id"] == wid)


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeOzon(mock)


PRODUCTS = [  # как после синхронизации товаров (шаг 1.2)
    (1001, 900001001, "6001-2RS.C3.SRL.NAC.TH"),
    (1002, 900001002, "ACB305523.NAC.JP"),
    (1003, 900001003, "6000-2RS.C3.SRL.NAC.JP"),
    (1004, 900001004, "6000.CM.NAC.JP"),
]


async def make_user(sessions, email="seller@example.com", with_products=True, with_key=True) -> uuid.UUID:
    async with sessions() as db:
        user = User(email=email, hashed_password="x")
        db.add(user)
        await db.flush()
        now = datetime.now(timezone.utc)
        if with_products:
            db.add_all(Product(user_id=user.id, ozon_product_id=pid, ozon_sku=sku, offer_id=offer, synced_at=now)
                       for pid, sku, offer in PRODUCTS)
        if with_key:
            db.add(ApiCredential(user_id=user.id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                                 is_active=True, expires_at=now + timedelta(days=60)))
        await db.commit()
        return user.id


async def sync(sessions, user_id) -> sync_mod.StockSyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_stocks(db, user_id, client)


async def warehouses(sessions, user_id) -> dict[int, Warehouse]:
    async with sessions() as db:
        res = await db.execute(select(Warehouse).where(Warehouse.user_id == user_id))
        return {w.ozon_warehouse_id: w for w in res.scalars()}


async def latest(sessions, user_id) -> dict[tuple[int, int], tuple]:
    """(ozon_product_id, ozon_warehouse_id) → (present, reserved, in_transit) последнего снепшота."""
    async with sessions() as db:
        rows = await stocks_service.latest_stocks(db, user_id)
        return {(p.ozon_product_id, w.ozon_warehouse_id): (s.present, s.reserved, s.in_transit) for s, p, w in rows}


async def snapshot_count(sessions) -> int:
    async with sessions() as db:
        return await db.scalar(select(func.count()).select_from(ProductStock))


# ── синхронизация ──
async def test_first_sync(sessions, ozon):
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {
        "warehouses_created": 5, "warehouses_updated": 0,
        "stocks_changed": 6, "stocks_unchanged": 0, "unknown_products": 0,  # чужой SKU мы у Ozon не запрашиваем
    }

    whs = await warehouses(sessions, user_id)
    assert (whs[EKB].type, whs[EKB].name, whs[EKB].cluster_name, whs[EKB].cluster_id) == (
        "FBO", "ЕКАТЕРИНБУРГ_РФЦ_НОВЫЙ", "Екатеринбург", 154,
    )
    assert whs[SPB].cluster_name == "Санкт-Петербург и СЗО"
    assert (whs[FBS_WH].type, whs[FBS_WH].is_active, whs[FBS_WH].cluster_name) == ("FBS", True, None)
    assert (whs[RFBS_WH].type, whs[RFBS_WH].is_active) == ("rFBS", False)  # статус disabled = «В архиве»

    stocks = await latest(sessions, user_id)
    assert stocks[(1001, EKB)] == (9, None, 0)  # FBO: резерв Ozon не отдаёт
    assert stocks[(1001, SPB)] == (10, None, 4)
    assert stocks[(1002, SPB)] == (5, None, 1)  # две строки одной пары (разные теги) сложены
    assert stocks[(1003, KRD)] == (0, None, 20)  # только в пути
    assert stocks[(1001, FBS_WH)] == (12, 2, None)  # FBS: есть резерв, нет «в пути»
    assert stocks[(1004, FBS_WH)] == (1, 0, None)

    # запросы по документации: skus строками, склады FBS — limit 200
    assert ozon.calls["fbo"] == [{"skus": ["900001001", "900001002", "900001003", "900001004"]}]
    assert ozon.calls["warehouses"] == [{"limit": 200}]
    assert [c["warehouse_id"] for c in ozon.calls["fbs"]] == [FBS_WH, RFBS_WH]


async def test_resync_without_changes(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id)
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {
        "warehouses_created": 0, "warehouses_updated": 0,
        "stocks_changed": 0, "stocks_unchanged": 6, "unknown_products": 0,
    }
    assert await snapshot_count(sessions) == 6


async def test_changes_zeroing_and_inactive_warehouse(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id)

    ozon.fbo_row(900001001, SPB)["available_stock_count"] = 7  # продали 3 шт.
    ozon.fbo["items"] = [i for i in ozon.fbo["items"] if i["warehouse_id"] != EKB]  # со склада всё вывезли
    stats = await sync(sessions, user_id)
    assert stats.stocks_changed == 2 and stats.stocks_unchanged == 4
    assert stats.warehouses_updated == 1  # Екатеринбург стал неактивным

    stocks = await latest(sessions, user_id)
    assert stocks[(1001, SPB)] == (7, None, 4)
    assert stocks[(1001, EKB)] == (0, None, 0)  # обнулили, а не оставили 9
    assert (await warehouses(sessions, user_id))[EKB].is_active is False

    # обнуление пишется один раз
    assert (await sync(sessions, user_id)).stocks_changed == 0


async def test_sku_batches_of_100(sessions, ozon, monkeypatch):
    monkeypatch.setattr(sync_mod, "SKU_BATCH", 3)
    user_id = await make_user(sessions)
    await sync(sessions, user_id)
    assert [c["skus"] for c in ozon.calls["fbo"]] == [["900001001", "900001002", "900001003"], ["900001004"]]
    assert sync_mod.SKU_BATCH == 3 and sync_mod.WAREHOUSE_PAGE == 200


async def test_fbs_pagination(sessions, ozon):
    w1, w2 = ozon.warehouses["warehouses"]
    ozon.warehouse_pages = {
        "": {"warehouses": [w1], "cursor": "p2", "has_next": True},
        "p2": {"warehouses": [w2], "cursor": "", "has_next": False},
    }
    s1, s2 = ozon.fbs_stocks[FBS_WH]["stocks"]
    ozon.stock_pages = {
        "": {"stocks": [s1], "cursor": "c2", "has_next": True},
        "c2": {"stocks": [s2], "cursor": "", "has_next": False},
    }
    user_id = await make_user(sessions)
    await sync(sessions, user_id)
    assert [c.get("cursor") for c in ozon.calls["warehouses"]] == [None, "p2"]
    assert [c["cursor"] for c in ozon.calls["fbs"] if c["warehouse_id"] == FBS_WH] == ["", "c2"]
    stocks = await latest(sessions, user_id)
    assert (1001, FBS_WH) in stocks and (1004, FBS_WH) in stocks


async def test_without_products_fbo_not_requested(sessions, ozon):
    user_id = await make_user(sessions, with_products=False)
    stats = await sync(sessions, user_id)
    assert ozon.calls["fbo"] == []  # SKU берутся из синхронизированных товаров
    assert stats.unknown_products == 2  # строки FBS по неизвестным товарам
    assert set(await warehouses(sessions, user_id)) == {FBS_WH, RFBS_WH}


async def test_run_sync_stocks_kind(sessions, ozon):
    user_id = await make_user(sessions)
    run = await runner.run_sync("stocks", user_id, sessions)
    assert run.status == SUCCESS and run.kind == "stocks" and run.stats["stocks_changed"] == 6


# ── API ──
def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def api_user(sessions, client, tokens, ozon) -> uuid.UUID:
    """Пользователь из фикстуры tokens + его товары, ключ и синхронизированные остатки."""
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    user_id = uuid.UUID(me["id"])
    async with sessions() as db:
        now = datetime.now(timezone.utc)
        db.add_all(Product(user_id=user_id, ozon_product_id=pid, ozon_sku=sku, offer_id=offer, synced_at=now)
                   for pid, sku, offer in PRODUCTS)
        db.add(ApiCredential(user_id=user_id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                             is_active=True, expires_at=now + timedelta(days=60)))
        await db.commit()
    await sync(sessions, user_id)
    return user_id


async def test_api_requires_auth(client):
    assert (await client.get("/api/v1/warehouses")).status_code == 401
    assert (await client.get("/api/v1/stocks")).status_code == 401


async def test_api_warehouses(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    data = (await client.get("/api/v1/warehouses", headers=auth(tokens))).json()
    by_id = {w["ozon_warehouse_id"]: w for w in data}
    assert by_id[SPB] | {"id": None, "synced_at": None} == {
        "id": None, "ozon_warehouse_id": SPB, "name": "СПБ_БУГРЫ_РФЦ", "type": "FBO",
        "cluster_name": "Санкт-Петербург и СЗО", "is_active": True, "synced_at": None,
        "products_in_stock": 2, "total_present": 15, "total_in_transit": 5,
    }
    assert by_id[KRD]["products_in_stock"] == 0 and by_id[KRD]["total_in_transit"] == 20
    assert data[-1]["ozon_warehouse_id"] == RFBS_WH  # неактивные — в конце


async def test_api_stocks_and_filters(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    get = lambda **q: client.get("/api/v1/stocks", params=q, headers=auth(tokens))  # noqa: E731

    rows = (await get()).json()
    assert len(rows) == 6
    row = next(r for r in rows if r["product"]["offer_id"] == "6001-2RS.C3.SRL.NAC.TH" and r["warehouse"]["type"] == "FBS")
    assert row["present"] == 12 and row["reserved"] == 2 and row["in_transit"] is None
    assert row["avg_daily_sales"] is None and row["days_of_supply"] is None and row["status"] is None  # продаж пока нет

    whs = {w["ozon_warehouse_id"]: w["id"] for w in (await client.get("/api/v1/warehouses", headers=auth(tokens))).json()}
    assert {r["product"]["offer_id"] for r in (await get(warehouse_id=whs[SPB])).json()} == {
        "6001-2RS.C3.SRL.NAC.TH", "ACB305523.NAC.JP",
    }
    product_id = row["product"]["id"]
    assert len((await get(product_id=product_id)).json()) == 3  # Екатеринбург, СПб, FBS
    assert len((await get(status="no_data")).json()) == 6
    assert (await get(status="critical")).json() == []

    r = await get(status="bad")
    assert r.status_code == 400 and r.json()["detail"] == "Статус: недопустимое значение"
    r = await get(warehouse_id="123")
    assert r.status_code == 400 and r.json()["detail"] == "Склад: некорректный идентификатор"


async def test_api_stocks_hides_empty_unless_asked(sessions, client, tokens, ozon):
    user_id = await api_user(sessions, client, tokens, ozon)
    ozon.fbo["items"] = [i for i in ozon.fbo["items"] if i["warehouse_id"] != EKB]
    await sync(sessions, user_id)
    assert len((await client.get("/api/v1/stocks", headers=auth(tokens))).json()) == 5
    rows = (await client.get("/api/v1/stocks", params={"include_empty": True}, headers=auth(tokens))).json()
    assert len(rows) == 6


async def test_api_status_when_sales_known(sessions, client, tokens, ozon, monkeypatch):
    """Продажи по товару в целом (шаг 3.5): дни запаса = остаток на всех складах / продажи в день."""
    user_id = await api_user(sessions, client, tokens, ozon)
    async with sessions() as db:
        rows = await stocks_service.latest_stocks(db, user_id)
    products = {p.ozon_product_id: p.id for _, p, _ in rows}
    total = {}
    for stock, p, _ in rows:
        total[p.ozon_product_id] = total.get(p.ozon_product_id, 0) + max(stock.present or 0, 0)
    sales = {products[1001]: Decimal(total[1001]) / 5}  # остатка на 5 дней — critical
    sales.update({pid: Decimal("0") for oid, pid in products.items() if oid != 1001})  # не продавались

    async def fake_sales(db, uid, days=30):
        return sales

    monkeypatch.setattr(stocks_service, "avg_daily_sales", fake_sales)
    critical = (await client.get("/api/v1/stocks", params={"status": "critical"}, headers=auth(tokens))).json()
    assert {r["product"]["id"] for r in critical} == {str(products[1001])}
    assert {(r["days_of_supply"], r["product_available"]) for r in critical} == {("5.0", total[1001])}
    excess = (await client.get("/api/v1/stocks", params={"status": "excess"}, headers=auth(tokens))).json()
    assert excess and all(r["days_of_supply"] is None and r["avg_daily_sales"] == "0" for r in excess)  # лежит без продаж
    # фильтр по складу не меняет дни запаса: они по товару на всех складах
    one = critical[0]["warehouse"]["id"]
    rows = (await client.get("/api/v1/stocks", params={"warehouse_id": one, "status": "critical"},
                             headers=auth(tokens))).json()
    assert [r["days_of_supply"] for r in rows] == ["5.0"]


async def test_api_isolation(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens, ozon)
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    t2 = (await client.post("/api/v1/auth/login", json=other)).json()
    assert (await client.get("/api/v1/warehouses", headers=auth(t2))).json() == []
    assert (await client.get("/api/v1/stocks", headers=auth(t2))).json() == []


async def test_api_manual_stocks_sync(sessions, client, tokens, ozon, monkeypatch):
    calls = []
    monkeypatch.setattr(sync_api, "enqueue_sync", lambda kind, u, r, params=None: calls.append(kind))
    await api_user(sessions, client, tokens, ozon)
    r = await client.post("/api/v1/sync/stocks", headers=auth(tokens))
    assert r.status_code == 202 and r.json()["kind"] == "stocks" and calls == ["stocks"]
    # синхронизация товаров идёт независимо от остатков
    assert (await client.post("/api/v1/sync/products", headers=auth(tokens))).status_code == 202
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["stocks"]["last_run"]["status"] == "queued"


# ── Celery ──
def test_beat_stocks_every_30_minutes():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sync-stocks-every-30-min"]
    assert entry["task"] == "tasks.sync_stocks_all"
    assert entry["schedule"].minute == {13, 43} and entry["schedule"].hour == set(range(24))
    assert tasks.FOR_USER_TASKS["stocks"] is tasks.sync_stocks_for_user


def test_stocks_fanout(monkeypatch):
    users = [uuid.uuid4(), uuid.uuid4()]

    async def fake_users():
        return users

    queued = []
    monkeypatch.setattr(tasks, "_active_users", fake_users)
    monkeypatch.setattr(tasks.sync_stocks_for_user, "delay", lambda uid: queued.append(uid))
    assert tasks.sync_stocks_all() == 2 and queued == [str(u) for u in users]


def test_fixtures_documented_format():
    for name in ("analytics_stocks", "warehouse_list", "warehouse_stocks"):
        text = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        assert '"total"' not in text
    assert copy.deepcopy(load("warehouse_list"))["warehouses"][0]["status"] == "created"
