"""Изменение цены одного товара (шаг 6.2): предпросмотр → применение по preview_id → price_changes, audit_log,
снимок цены; отказ и ошибки Ozon, блокировка проверками, устаревший и чужой предпросмотр. Ozon — фейковый."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import settings
from app.integrations.ozon import client as client_mod
from app.models.audit import AuditLog
from app.models.price_change import PriceChange
from app.models.product import Product, ProductPrice
from app.services.sync.finance import today_msk
from tests.test_pricing import add_sales, auth, user_id_of
from tests.test_sync_promotions import setup_user

PRICES = "/v5/product/info/prices"
IMPORT = "/v1/product/import/prices"
OFFER = "6001-2RS.C3.SRL.NAC.TH"


class PriceOzon:
    """Фейковый Ozon: цены товаров и приём новой цены. После успеха цена у Ozon меняется (как через 1–3 минуты)."""

    def __init__(self, mock: respx.MockRouter) -> None:
        self.items = {
            1001: {"price": "540", "old_price": "720", "min_price": "400", "marketing_seller_price": "540"},
            1002: {"price": "2150", "old_price": "0", "min_price": "0", "marketing_seller_price": "2150"},
            1003: {"price": "499", "old_price": "650", "min_price": "0", "marketing_seller_price": "0"},
        }
        self.imports: list[dict] = []
        self.import_answer = lambda body: {"result": [
            {"product_id": p["product_id"], "offer_id": "", "updated": True, "errors": []} for p in body["prices"]]}
        self.import_fail: int | Exception | None = None
        self.prices_fail: int | None = None
        self.fail_next_batch = False  # следующий запрос цен (и все его повторы) — ошибка Ozon 500
        self._failing: dict | None = None
        mock.post(PRICES).mock(side_effect=self._prices)
        mock.post(IMPORT).mock(side_effect=self._import)

    def _prices(self, request: httpx.Request) -> httpx.Response:
        if self.prices_fail:
            return httpx.Response(self.prices_fail, json={"code": self.prices_fail, "message": "internal error"})
        ids = [int(i) for i in json.loads(request.content)["filter"]["product_id"]]
        items = [{"product_id": pid, "acquiring": float(D(self.items[pid]["price"]) * D("0.015")),  # ₽: 1,5 % от цены
                  "price": {**self.items[pid], "currency_code": "RUB"},
                  "commissions": {"sales_percent_fbo": 38}} for pid in ids if pid in self.items]
        return httpx.Response(200, json={"items": items, "cursor": "", "total_items": len(items)})

    def _import(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.imports.append(body)
        if self.fail_next_batch:
            self._failing, self.fail_next_batch = body, False
        if self._failing is not None and body == self._failing:
            return httpx.Response(500, json={"code": 500, "message": "internal error"})
        if isinstance(self.import_fail, Exception):
            raise self.import_fail
        if self.import_fail:
            return httpx.Response(self.import_fail, json={"code": self.import_fail, "message": "internal error"})
        answer = self.import_answer(body)
        for p, res in zip(body["prices"], answer["result"]):
            if res.get("updated"):
                self.items[p["product_id"]]["price"] = p["price"]
        return httpx.Response(200, json=answer)


@pytest.fixture
def ozon(monkeypatch):
    monkeypatch.setattr(client_mod, "NETWORK_BACKOFF", (0, 0, 0))  # повторы клиента — без ожидания
    monkeypatch.setattr(client_mod, "SERVER_RETRY_DELAY", 0)
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield PriceOzon(mock)


async def ready(client, tokens, sessions) -> dict:
    """Ключ Ozon, товары по 540 ₽ (мин. цена 400, у 1001 зачёркнутая 720), себестоимость 164 ₽ × 1,22 у 1001 и 1003,
    у 1001 продажи — логистика 30 ₽ на штуку."""
    user_id = await setup_user(sessions, await user_id_of(client, tokens))
    for offer in (OFFER, "6000-2RS.C3.SRL.NAC.JP"):
        await client.put(f"/api/v1/costs/{offer}", json={"cost_price": "164", "vat_multiplier": "1.22"}, headers=auth(tokens))
    async with sessions() as db:
        rows = {p.ozon_product_id: p for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()}
        await add_sales(db, user_id, rows[1001], today_msk() - timedelta(days=1), logistics=D("30"))
        await db.commit()
        products = {pid: p.id for pid, p in rows.items()}
    return {"user_id": user_id, "products": products}


async def preview(client, tokens, env, price, pid=1001, **kw):
    return await client.post("/api/v1/prices/preview", json={"product_id": str(env["products"][pid]), "price": price, **kw},
                             headers={**auth(tokens), "user-agent": "pytest-browser"})


async def apply(client, tokens, preview_id):
    return await client.post("/api/v1/prices/apply", json={"preview_id": str(preview_id)},
                             headers={**auth(tokens), "user-agent": "pytest-browser"})


async def changes(sessions, user_id) -> list[PriceChange]:
    async with sessions() as db:
        return list((await db.execute(select(PriceChange).where(PriceChange.user_id == user_id)
                                      .order_by(PriceChange.created_at))).scalars())


async def journal(sessions, user_id, entity_type="product_price") -> list[AuditLog]:
    async with sessions() as db:
        return list((await db.execute(select(AuditLog).where(AuditLog.user_id == user_id, AuditLog.entity_type == entity_type)
                                      .order_by(AuditLog.created_at))).scalars())


async def latest_price(sessions, product_id) -> ProductPrice:
    async with sessions() as db:
        return (await db.execute(select(ProductPrice).where(ProductPrice.product_id == product_id)
                                 .order_by(ProductPrice.snapshot_at.desc()).limit(1))).scalar_one()


async def test_preview_then_apply(client, tokens, sessions, ozon):
    env = await ready(client, tokens, sessions)
    r = await preview(client, tokens, env, "600", reason="поднять маржу")
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["can_apply"], p["errors"], p["price_fresh"]) == (True, [], True)
    assert (p["price_before"], p["price_after"], p["change_pct"], p["change_rub"]) == ("540.00", "600", "11.1", "60.00")
    assert (p["old_price"], p["min_price_ozon"], p["floor"]) == ("720.00", "400.00", "210.08")
    assert (p["allowed_from"], p["allowed_to"]) == ("432.00", "648.00")
    # калькулятор 4.2 (комиссия 38 %, эквайринг 1,5 %, логистика 30 ₽, налог не задан):
    # 540 × 0,605 − 200,08 − 30 = 96,62 ₽ (17,89 %) → 600 × 0,605 − 230,08 = 132,92 ₽ (22,15 %)
    assert (p["profit_before"], p["margin_before"], p["profit_after"], p["margin_after"]) == (
        "96.62", "17.89", "132.92", "22.15")
    assert p["min_price"] and p["target_price"] and p["margin_note"] is None
    assert p["reason"] == "поднять маржу" and p["product"]["offer_id"] == OFFER
    assert ozon.imports == []  # предпросмотр в Ozon ничего не меняет
    [pv] = await changes(sessions, env["user_id"])
    assert (pv.status, pv.is_dry_run, pv.is_applied, pv.price_before, pv.price_after) == ("preview", True, False, D("540"), D("600"))
    # текущая цена — свежая из Ozon: зачёркнутая 720 появилась — новый снимок
    assert (await latest_price(sessions, env["products"][1001])).old_price == D("720")

    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["applied"] and res["status"] == "applied" and "1–3 минуты" in res["message"]
    assert ozon.imports == [{"prices": [{"product_id": 1001, "price": "600.00", "currency_code": "RUB"}]}]
    pv_row, attempt = await changes(sessions, env["user_id"])
    assert (attempt.status, attempt.is_applied, attempt.is_dry_run, attempt.preview_id) == ("applied", True, False, pv_row.id)
    assert (attempt.price_before, attempt.price_after, attempt.reason, attempt.change_type) == (
        D("540"), D("600"), "поднять маржу", "manual_single")
    [entry] = await journal(sessions, env["user_id"])
    assert (entry.action, entry.status, entry.entity_id, entry.product_id) == (
        "price.update", "applied", str(attempt.id), env["products"][1001])
    assert entry.before_json == {"price": "540.00", "min_price": "400.00"} and entry.after_json["price"] == "600.00"
    assert entry.ozon_request["path"] == IMPORT and entry.ozon_response["result"][0]["updated"] is True
    assert entry.user_agent == "pytest-browser"
    [http] = [a for a in await journal(sessions, env["user_id"], "http") if a.path == "/api/v1/prices/apply"]
    assert http.request_id == entry.request_id and http.http_status == 200
    assert not [a for a in await journal(sessions, env["user_id"], "http") if a.path == "/api/v1/prices/preview"]
    snap = await latest_price(sessions, env["products"][1001])  # новый снимок — поставленная цена
    assert (snap.price, snap.old_price, snap.min_price, snap.commission_pct) == (D("600"), D("720"), D("400"), D("38"))
    log = (await client.get("/api/v1/audit/prices", params={"kind": "price"}, headers=auth(tokens))).json()
    assert [(i["action_title"], i["status"], i["request"]["http_status"]) for i in log["items"]] == [
        ("Изменение цены", "applied", 200)]

    # тот же предпросмотр второй раз — нельзя
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 409 and "уже применяли" in r.json()["detail"] and len(ozon.imports) == 1
    # пауза час: следующий предпросмотр того же товара заблокирован
    p2 = (await preview(client, tokens, env, "610")).json()
    assert not p2["can_apply"] and any("меняли 0 мин. назад" in e for e in p2["errors"]) and p2["next_change_at"]
    assert (await apply(client, tokens, p2["preview_id"])).status_code == 400


async def test_ozon_rejects(client, tokens, sessions, ozon):
    env = await ready(client, tokens, sessions)
    ozon.import_answer = lambda body: {"result": [{"product_id": 1001, "updated": False, "errors": [
        {"code": "price_out_of_range", "message": "price is out of range"}]}]}
    p = (await preview(client, tokens, env, "600")).json()
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 409 and r.json()["detail"].startswith("Ozon не изменил цену: цена выходит за порог категории")
    attempt = (await changes(sessions, env["user_id"]))[-1]
    assert (attempt.status, attempt.is_applied) == ("rejected", False) and "порог" in attempt.error
    [entry] = await journal(sessions, env["user_id"])
    assert entry.status == "rejected" and entry.ozon_response["result"][0]["errors"][0]["code"] == "price_out_of_range"
    assert (await latest_price(sessions, env["products"][1001])).price == D("540")  # цена не менялась
    # отказ Ozon паузу не ставит; слишком частые изменения — понятным текстом
    ozon.import_answer = lambda body: {"result": [{"product_id": 1001, "updated": False, "errors": [
        {"code": "", "message": "error limiting: acquire limit per item: items limit: limit exceeded"}]}]}
    p = (await preview(client, tokens, env, "600")).json()
    assert p["can_apply"], p["errors"]
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 409 and "не больше 10 раз в час" in r.json()["detail"]


async def test_ozon_fails_or_times_out(client, tokens, sessions, ozon):
    env = await ready(client, tokens, sessions)
    ozon.import_fail = 500
    p = (await preview(client, tokens, env, "600")).json()
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 502 and "ничего не изменилось" in r.json()["detail"]
    attempt = (await changes(sessions, env["user_id"]))[-1]
    assert attempt.status == "failed"
    assert (await journal(sessions, env["user_id"]))[-1].status == "failed"
    # ошибка запроса — паузы нет; Ozon не ответил — могло примениться: пауза есть
    ozon.import_fail = httpx.ReadTimeout("timeout")
    p = (await preview(client, tokens, env, "600")).json()
    assert p["can_apply"], p["errors"]
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 504 and "могла измениться" in r.json()["detail"]
    assert (await changes(sessions, env["user_id"]))[-1].status == "unknown"
    assert (await journal(sessions, env["user_id"]))[-1].status == "unknown"
    p = (await preview(client, tokens, env, "590")).json()
    assert any("меняли" in e for e in p["errors"])


async def test_blocked_by_checks(client, tokens, sessions, ozon):
    env = await ready(client, tokens, sessions)
    p = (await preview(client, tokens, env, "700")).json()  # +29,6 % (зачёркнутая 720 — выше, не мешает)
    assert not p["can_apply"] and any("больше 20 % за раз" in e for e in p["errors"])
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 400 and "Предпросмотр показал ошибки" in r.json()["detail"]
    p = (await preview(client, tokens, env, "200")).json()  # ниже себестоимости с НДС + 5 %
    assert any("ниже себестоимости" in e for e in p["errors"])
    ozon.items[1001]["old_price"] = "590"
    p = (await preview(client, tokens, env, "600")).json()  # зачёркнутую сервис не меняет — блокировка
    assert any("не ниже цены до скидки (590,00 ₽" in e for e in p["errors"]) and p["allowed_to"] == "589.99"
    p = (await preview(client, tokens, env, "600", pid=1002)).json()  # нет себестоимости
    assert any("Нет себестоимости" in e for e in p["errors"])
    assert ozon.imports == []

    # цена в Ozon изменилась после предпросмотра — применить нельзя, проверки заново
    ozon.items[1001]["old_price"] = "720"
    p = (await preview(client, tokens, env, "600")).json()
    assert p["can_apply"]
    ozon.items[1001]["price"] = "560"
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 400 and "изменилась после предпросмотра: была 540,00 ₽, сейчас 560,00 ₽" in r.json()["detail"]
    assert ozon.imports == []
    attempt = (await changes(sessions, env["user_id"]))[-1]
    assert attempt.status == "blocked" and attempt.errors and attempt.preview_id is not None
    entry = (await journal(sessions, env["user_id"]))[-1]
    assert entry.status == "rejected" and entry.ozon_request is None

    async with sessions() as db:  # товар в архиве Ozon
        product = await db.get(Product, env["products"][1003])
        product.is_archived = True
        await db.commit()
    p = (await preview(client, tokens, env, "520", pid=1003)).json()
    assert p["errors"][0].startswith("Товар в архиве Ozon")


async def test_preview_expired_foreign_or_missing(client, tokens, sessions, ozon):
    env = await ready(client, tokens, sessions)
    p = (await preview(client, tokens, env, "600")).json()
    async with sessions() as db:  # прошло 16 минут
        row = await db.get(PriceChange, uuid.UUID(p["preview_id"]))
        row.created_at = row.created_at - timedelta(minutes=16)
        await db.commit()
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 410 and "устарел" in r.json()["detail"] and ozon.imports == []

    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    p = (await preview(client, tokens, env, "600")).json()
    r = await apply(client, t2, p["preview_id"])
    assert r.status_code == 404 and "Предпросмотр не найден" in r.json()["detail"]
    assert (await preview(client, t2, env, "600")).status_code == 404  # чужой товар
    assert (await apply(client, tokens, uuid.uuid4())).status_code == 404
    # применить без предпросмотра нельзя: только preview_id
    r = await client.post("/api/v1/prices/apply", json={"product_id": str(env["products"][1001]), "price": "600"},
                          headers=auth(tokens))
    assert r.status_code == 400 and "Предпросмотр" in r.json()["detail"]
    assert (await apply(client, tokens, p["preview_id"])).status_code == 200  # свой — можно
    assert (await preview(client, tokens, env, "0")).status_code == 400
    r = await preview(client, tokens, env, "600.125")
    assert r.status_code == 400 and "знаков после запятой" in r.json()["detail"]


async def test_ozon_unavailable_and_no_key(client, tokens, sessions, ozon):
    env = await ready(client, tokens, sessions)
    ozon.prices_fail = 500
    r = await preview(client, tokens, env, "600")
    assert r.status_code == 200
    p = r.json()  # цену из Ozon сейчас не получить — по последнему снимку, с предупреждением
    assert (p["price_fresh"], p["price_before"], p["can_apply"]) == (False, "540.00", True)
    assert any("по данным на" in w for w in p["warnings"])
    r = await apply(client, tokens, p["preview_id"])  # применять — только по свежей цене
    assert r.status_code == 502 and "Не удалось проверить текущую цену в Ozon" in r.json()["detail"]
    assert ozon.imports == [] and (await changes(sessions, env["user_id"]))[-1].status == "failed"

    await client.post("/api/v1/auth/register", json={"email": "nokey@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "nokey@example.com", "password": "strongpass1"})).json()
    async with sessions() as db:
        uid = await user_id_of(client, t2)
        product = Product(user_id=uid, ozon_product_id=5, offer_id="X-5", synced_at=datetime.now(timezone.utc))
        db.add(product)
        await db.commit()
    r = await client.post("/api/v1/prices/preview", json={"product_id": str(product.id), "price": "100"}, headers=auth(t2))
    assert r.status_code == 400 and "API-ключ Ozon" in r.json()["detail"]
