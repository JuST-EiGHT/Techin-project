"""Акции Ozon: маржа, синхронизация на записанных ответах, API, Celery."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import settings
from app.engine.promotions import is_whole_stock, margin_level
from app.integrations.ozon.client import OzonClient
from app.models.api_credential import ApiCredential
from app.models.product import Product, ProductPrice
from app.models.product_cost import BEGINNING, ProductCost
from app.models.promotion import Promotion, PromotionProduct
from app.models.sync_run import SUCCESS
from app.models.user import User
from app.models.warehouse import ProductStock, Warehouse
from app.services.encryption import encrypt
from app.services.sync import promotions as sync_mod
from app.services.sync import runner
from app.services.sync.finance import today_msk
from app.tasks import sync as tasks
from tests.test_pricing import add_sales

FIXTURES = Path(__file__).parent / "fixtures" / "ozon" / "actions"
API_KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ── расчёты ──
def test_margin_level_and_whole_stock():
    assert [margin_level(m) for m in (None, Decimal("-0.01"), Decimal("9.99"), Decimal("10"))] == [None, "negative", "low", "ok"]
    assert is_whole_stock("Скидка на сток", "DISCOUNT") and is_whole_stock("Акция", "ELASTIC_BOOSTING")
    assert is_whole_stock("ЭЛАСТИЧНЫЙ БУСТИНГ x2", None) and not is_whole_stock("Осенняя распродажа", "DISCOUNT")


@pytest.mark.parametrize("mode,expected", [
    ("SELLER", False), ("MANUAL", False), ("NOT_SET", None), (None, None), ("", None), ("AUTO_ADDED", True), ("OZON", None),
])
def test_auto_by_mode(mode, expected):
    assert sync_mod.auto_by_mode(mode) is expected


# ── фейковый Ozon ──
class FakeOzon:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.actions = load("actions")
        self.products = {71001: load("products_71001"), 71002: load("products_71002_v1")}
        self.candidates = {71001: load("candidates_71001"), 71003: load("candidates_71003")}
        self.auto_add = {(71001, "2026-10-01T00:00:00Z"): load("auto_add_71001")}
        self.calls: dict[str, list] = {"products": [], "candidates": [], "auto": []}
        self.product_pages: dict[int, dict[str, dict]] = {}  # action_id → {last_id: страница}
        mock.get("/v1/actions").mock(side_effect=lambda r: httpx.Response(200, json=self.actions))
        mock.post("/v2/actions/products").mock(side_effect=self._products)
        mock.post("/v2/actions/candidates").mock(side_effect=self._candidates)
        mock.post("/v2/actions/auto-add/products/list").mock(side_effect=self._auto)

    @staticmethod
    def _empty():
        return {"products": [], "total": 0, "last_id": ""}

    def _products(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["products"].append(body)
        if body["action_id"] in self.product_pages:
            return httpx.Response(200, json=self.product_pages[body["action_id"]][body["last_id"]])
        return httpx.Response(200, json=self.products.get(body["action_id"], self._empty()))

    def _candidates(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["candidates"].append(body)
        return httpx.Response(200, json=self.candidates.get(body["action_id"], self._empty()))

    def _auto(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["auto"].append(body)
        return httpx.Response(200, json=self.auto_add.get((body["action_id"], body["auto_add_date"]), {"products": [], "total": 0}))


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeOzon(mock)


PRODUCTS = [(1001, 900001001, "6001-2RS.C3.SRL.NAC.TH"), (1002, 900001002, "ACB305523.NAC.JP"), (1003, 900001003, "6000-2RS.C3.SRL.NAC.JP")]


async def setup_user(sessions, user_id=None, email="seller@example.com") -> uuid.UUID:
    """Пользователь (или существующий по id) + товары с ценами (комиссия 38 %, эквайринг 1.5 %) + ключ."""
    async with sessions() as db:
        now = datetime.now(timezone.utc)
        if user_id is None:
            user = User(email=email, hashed_password="x")
            db.add(user)
            await db.flush()
            user_id = user.id
        for pid, sku, offer in PRODUCTS:
            p = Product(user_id=user_id, ozon_product_id=pid, ozon_sku=sku, offer_id=offer, name=f"Подшипник {offer}", synced_at=now)
            db.add(p)
            await db.flush()
            db.add(ProductPrice(product_id=p.id, snapshot_at=now, price=Decimal("540"), min_price=Decimal("400"),
                                commission_pct=Decimal("38"), acquiring_pct=Decimal("1.5")))
        db.add(ApiCredential(user_id=user_id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                             is_active=True, expires_at=now + timedelta(days=60)))
        await db.commit()
    return user_id


async def sync(sessions, user_id) -> sync_mod.PromotionSyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_promotions(db, user_id, client)


async def promo_rows(sessions, user_id) -> dict[tuple[int, int], PromotionProduct]:
    async with sessions() as db:
        res = await db.execute(
            select(PromotionProduct, Promotion.ozon_action_id, Product.ozon_product_id)
            .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
            .join(Product, Product.id == PromotionProduct.product_id)
            .where(Promotion.user_id == user_id)
        )
        return {(a, p): r for r, a, p in res.all()}


async def promotions(sessions, user_id) -> dict[int, Promotion]:
    async with sessions() as db:
        return {p.ozon_action_id: p for p in (await db.execute(select(Promotion).where(Promotion.user_id == user_id))).scalars()}


# ── синхронизация ──
async def test_first_sync(sessions, ozon):
    user_id = await setup_user(sessions)
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {
        "actions": 4, "participating_actions": 2, "whole_stock_actions": 2,
        "products_active": 3, "candidates": 3, "auto_added": 2, "unknown_products": 1,
    }
    promos = await promotions(sessions, user_id)
    p = promos[71001]
    assert (p.title, p.discount_type, p.discount_value, p.is_participating, p.whole_stock) == (
        "Скидка на сток", "PERCENT", Decimal("15.00"), True, True,
    )
    assert p.freeze_date is None and p.auto_add_dates == ["2026-10-01T00:00:00Z", "2099-12-01T00:00:00Z"]
    assert promos[71002].whole_stock is True and promos[71002].freeze_date is not None
    assert promos[71003].whole_stock is False and promos[71003].is_participating is False

    rows = await promo_rows(sessions, user_id)
    r = rows[(71001, 1001)]
    assert (r.is_active, r.added_automatically, r.add_mode) == (True, False, "SELLER")  # добавил продавец
    assert (r.action_price, r.price, r.min_price, r.max_action_price) == (
        Decimal("459.00"), Decimal("540.00"), Decimal("420.00"), Decimal("470.00"),
    )
    assert r.margin_in_promo is None  # себестоимости ещё нет
    assert rows[(71001, 1002)].added_automatically is True  # NOT_SET, но в списке автодобавления
    assert rows[(71002, 1003)].added_automatically is True  # add_mode с AUTO (формат v1: числа, result)
    assert rows[(71002, 1003)].action_price == Decimal("380.00")
    cand = rows[(71001, 1003)]
    assert (cand.is_active, cand.added_automatically) == (False, False)
    assert cand.min_price == Decimal("400.00")  # min_seller_price = 0 → мин. цена товара из Ozon
    async with sessions() as db:  # и мин. цена товара 0 («не задана») → пусто, а не 0 ₽
        for pp in (await db.execute(select(ProductPrice))).scalars():
            pp.min_price = Decimal("0")
        await db.commit()
    await sync(sessions, user_id)
    assert (await promo_rows(sessions, user_id))[(71001, 1003)].min_price is None
    assert {k for k in rows if k[0] == 71003} == {(71003, 1001), (71003, 1002)}  # только кандидаты

    # запросы по документации: limit 100, last_id; автодобавление — только на наступившие даты
    assert {"action_id": 71001, "last_id": "", "limit": 100} in ozon.calls["products"]
    assert {c["auto_add_date"] for c in ozon.calls["auto"]} == {"2026-10-01T00:00:00Z"}  # будущая дата не запрошена


async def test_margin_with_costs(sessions, ozon):
    """Маржа в акции — калькулятор цены (шаг 4.2): себестоимость, комиссия 38 % и эквайринг 1,5 % из Ozon, логистика —
    по продажам товара, у кого продаж нет — средняя по магазину."""
    user_id = await setup_user(sessions)
    today = today_msk()
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        products = {p.ozon_product_id: p for p in (await db.execute(select(Product))).scalars()}
        for pid, cost in ((1001, "164"), (1003, "400")):
            db.add(ProductCost(user_id=user_id, offer_id=products[pid].offer_id, cost_price=Decimal(cost),
                               vat_multiplier=Decimal("1.22"), effective_from=BEGINNING, source="manual", created_at=now))
        await add_sales(db, user_id, products[1001], today - timedelta(days=2), qty=2)  # логистика 60 ₽ на штуку
        await db.commit()
    await sync(sessions, user_id)
    rows = await promo_rows(sessions, user_id)
    # 459 × (1 − 0,38 − 0,015) − (164 × 1,22 + 60) = 277,695 − 260,08 = 17,615 ₽ → 3,84 % (раньше без логистики — 16,91 %)
    assert rows[(71001, 1001)].margin_in_promo == Decimal("3.84")
    assert rows[(71002, 1003)].margin_in_promo < 0  # 380 ₽ при себестоимости 400 — в минус
    assert rows[(71001, 1002)].margin_in_promo is None  # себестоимость неизвестна


async def test_resync_removed_product_and_sticky_auto(sessions, ozon):
    user_id = await setup_user(sessions)
    await sync(sessions, user_id)
    ozon.auto_add = {}  # список автодобавления больше не возвращает 1002 — признак сохраняется
    ozon.candidates[71003]["products"] = ozon.candidates[71003]["products"][:1]  # 1002 больше не кандидат
    await sync(sessions, user_id)
    rows = await promo_rows(sessions, user_id)
    assert rows[(71001, 1002)].added_automatically is True
    assert (71003, 1002) not in rows

    ozon.products[71001]["products"][1]["add_mode"] = "SELLER"  # Ozon явно сообщил: добавил продавец
    await sync(sessions, user_id)
    assert (await promo_rows(sessions, user_id))[(71001, 1002)].added_automatically is False


async def test_pagination_last_id(sessions, ozon, monkeypatch):
    monkeypatch.setattr(sync_mod, "PAGE", 1)
    first, second, _ = ozon.products[71001]["products"]
    ozon.product_pages[71001] = {"": {"products": [first], "last_id": "p2"}, "p2": {"products": [second], "last_id": ""}}
    user_id = await setup_user(sessions)
    await sync(sessions, user_id)
    assert [c["last_id"] for c in ozon.calls["products"] if c["action_id"] == 71001] == ["", "p2"]
    assert {k for k in await promo_rows(sessions, user_id) if k[0] == 71001 and k[1] != 1003} == {(71001, 1001), (71001, 1002)}


async def test_run_sync_promotions(sessions, ozon):
    user_id = await setup_user(sessions)
    run = await runner.run_sync("promotions", user_id, sessions)
    assert run.status == SUCCESS and run.kind == "promotions" and run.stats["actions"] == 4


# ── API ──
def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def api_user(sessions, client, tokens) -> uuid.UUID:
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    user_id = await setup_user(sessions, uuid.UUID(me["id"]))
    await sync(sessions, user_id)
    return user_id


async def test_api_requires_auth(client):
    for url in ("/api/v1/promotions", "/api/v1/promotions/auto-added", f"/api/v1/promotions/{uuid.uuid4()}/products"):
        assert (await client.get(url)).status_code == 401


async def test_api_list(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens)
    data = (await client.get("/api/v1/promotions", headers=auth(tokens))).json()
    assert [p["title"] for p in data] == ["Эластичный бустинг", "Скидка на сток", "Осенняя распродажа"]  # закончившаяся скрыта
    stock = next(p for p in data if p["ozon_action_id"] == 71001)
    assert stock["whole_stock"] is True and "весь остаток" in stock["whole_stock_warning"]
    assert (stock["products_active"], stock["candidates"], stock["auto_added"], stock["min_margin"]) == (2, 1, 1, None)
    assert stock["is_frozen"] is False
    boost = next(p for p in data if p["ozon_action_id"] == 71002)
    assert boost["is_frozen"] is True  # freeze_date прошла
    autumn = next(p for p in data if p["ozon_action_id"] == 71003)
    assert autumn["whole_stock_warning"] is None and autumn["products_active"] == 0
    all_ = (await client.get("/api/v1/promotions", params={"include_finished": True}, headers=auth(tokens))).json()
    assert len(all_) == 4


async def test_api_products(sessions, client, tokens, ozon):
    user_id = await api_user(sessions, client, tokens)
    async with sessions() as db:  # остаток 1001 на складе — сколько может уйти по акции «весь остаток»
        wh = Warehouse(user_id=user_id, ozon_warehouse_id=1, name="СПБ", type="FBO", synced_at=datetime.now(timezone.utc))
        db.add(wh)
        await db.flush()
        pid = (await db.execute(select(Product.id).where(Product.user_id == user_id, Product.ozon_product_id == 1001))).scalar_one()
        db.add(ProductStock(product_id=pid, warehouse_id=wh.id, snapshot_at=datetime.now(timezone.utc), present=17, in_transit=0))
        await db.commit()

    promo_id = next(p["id"] for p in (await client.get("/api/v1/promotions", headers=auth(tokens))).json() if p["ozon_action_id"] == 71001)
    data = (await client.get(f"/api/v1/promotions/{promo_id}/products", headers=auth(tokens))).json()
    assert data["promotion"]["title"] == "Скидка на сток" and "себестоимост" in data["margin_note"]
    assert [i["product"]["offer_id"] for i in data["items"]] == [
        "6001-2RS.C3.SRL.NAC.TH", "ACB305523.NAC.JP", "6000-2RS.C3.SRL.NAC.JP",  # участники, затем кандидат
    ]
    first, second = data["items"][0], data["items"][1]
    assert (first["discount_pct"], first["stock_on_hand"], first["added_automatically"], first["margin_level"]) == ("15.0", 17, False, None)
    assert second["added_automatically"] is True and second["below_min_price"] is True  # 1800 < 1900

    only = (await client.get(f"/api/v1/promotions/{promo_id}/products", params={"participating": False}, headers=auth(tokens))).json()
    assert [i["product"]["offer_id"] for i in only["items"]] == ["6000-2RS.C3.SRL.NAC.JP"]
    r = await client.get(f"/api/v1/promotions/{promo_id}/products", params={"participating": "maybe"}, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"] == "Участие: нужно true или false"


async def test_api_auto_added(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens)
    data = (await client.get("/api/v1/promotions/auto-added", headers=auth(tokens))).json()
    assert sorted((i["promotion"]["title"], i["product"]["offer_id"]) for i in data["items"]) == [
        ("Скидка на сток", "ACB305523.NAC.JP"), ("Эластичный бустинг", "6000-2RS.C3.SRL.NAC.JP"),
    ]
    assert all(i["added_automatically"] and i["is_active"] for i in data["items"])
    assert data["items"][0]["promotion"]["whole_stock"] is True


async def test_api_isolation_and_404(sessions, client, tokens, ozon):
    await api_user(sessions, client, tokens)
    promo_id = (await client.get("/api/v1/promotions", headers=auth(tokens))).json()[0]["id"]
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    t2 = (await client.post("/api/v1/auth/login", json=other)).json()
    assert (await client.get("/api/v1/promotions", headers=auth(t2))).json() == []
    r = await client.get(f"/api/v1/promotions/{promo_id}/products", headers=auth(t2))
    assert r.status_code == 404 and r.json()["detail"] == "Акция не найдена"
    assert (await client.get("/api/v1/promotions/auto-added", headers=auth(t2))).json()["items"] == []


async def test_api_manual_sync(sessions, client, tokens, ozon, monkeypatch):
    calls = []
    from app.api.v1 import sync as sync_api

    monkeypatch.setattr(sync_api, "enqueue_sync", lambda kind, u, r, params=None: calls.append(kind))
    await api_user(sessions, client, tokens)
    r = await client.post("/api/v1/sync/promotions", headers=auth(tokens))
    assert r.status_code == 202 and r.json()["kind"] == "promotions" and calls == ["promotions"]
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["promotions"]["last_run"]["status"] == "queued"


# ── Celery ──
def test_beat_every_3_hours():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sync-promotions-every-3h"]
    assert entry["task"] == "tasks.sync_promotions_all"
    assert entry["schedule"].hour == {0, 3, 6, 9, 12, 15, 18, 21} and entry["schedule"].minute == {33}
    assert tasks.FOR_USER_TASKS["promotions"] is tasks.sync_promotions_for_user


def test_promotions_fanout(monkeypatch):
    users = [uuid.uuid4()]

    async def fake_users():
        return users

    queued = []
    monkeypatch.setattr(tasks, "_active_users", fake_users)
    monkeypatch.setattr(tasks.sync_promotions_for_user, "delay", lambda uid: queued.append(uid))
    assert tasks.sync_promotions_all() == 1 and queued == [str(users[0])]


async def test_api_margin_is_fresh(sessions, client, tokens, ozon):
    """Маржа в API акций считается на момент запроса: себестоимость и реклама поменялись — без новой синхронизации."""
    user_id = await api_user(sessions, client, tokens)
    async with sessions() as db:
        product = (await db.execute(select(Product).where(Product.user_id == user_id, Product.ozon_product_id == 1001))).scalar_one()
        await add_sales(db, user_id, product, today_msk() - timedelta(days=1))
        await db.commit()
    promo_id = next(p["id"] for p in (await client.get("/api/v1/promotions", headers=auth(tokens))).json() if p["ozon_action_id"] == 71001)
    url = f"/api/v1/promotions/{promo_id}/products"
    first = (await client.get(url, headers=auth(tokens))).json()["items"][0]
    assert first["margin_in_promo"] is None  # себестоимости нет
    await client.put("/api/v1/costs/6001-2RS.C3.SRL.NAC.TH", json={"cost_price": "164", "vat_multiplier": "1.22"}, headers=auth(tokens))
    data = (await client.get(url, headers=auth(tokens))).json()
    assert data["items"][0]["margin_in_promo"] == "3.84" and data["items"][0]["margin_level"] == "low"
    assert data["promotion"]["min_margin"] == "3.84"
    # реклама 5 % от цены: 17,615 − 459 × 5 % = −5,335 ₽ → −1,16 %
    await client.patch("/api/v1/settings", json={"price_ad_pct": "5"}, headers=auth(tokens))
    data = (await client.get(url, headers=auth(tokens))).json()
    assert data["items"][0]["margin_in_promo"] == "-1.16" and data["items"][0]["margin_level"] == "negative"
