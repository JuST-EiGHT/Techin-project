"""Вход и выход из акции через Ozon (шаг 4.3): предпросмотр → confirm → audit_log, жёсткие проверки, ответы Ozon."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from app.api.v1.promotions import is_urgent
from app.core.config import settings
from app.models.audit import AuditLog
from app.models.product import Product
from app.models.promotion import Promotion, PromotionPriceSnapshot, PromotionProduct
from app.services.sync.finance import today_msk
from tests.test_pricing import add_sales
from tests.test_sync_promotions import FakeOzon, api_user, auth

UPDATE = "/v1/actions/products/update"
DEACTIVATE = "/v2/actions/products/deactivate"
OFFERS = {1001: "6001-2RS.C3.SRL.NAC.TH", 1002: "ACB305523.NAC.JP", 1003: "6000-2RS.C3.SRL.NAC.JP"}


class WritableOzon(FakeOzon):
    """Фейковый Ozon, который принимает вход и выход из акции; ответы можно подменить."""

    def __init__(self, mock: respx.MockRouter) -> None:
        super().__init__(mock)
        self.writes: list[tuple[str, dict]] = []
        self.update_response = lambda body: {
            "active_product_ids": [str(p["product_id"]) for p in body["products"]], "deactivated_product_ids": [],
            "rejected": [], "warnings": [],
        }
        self.deactivate_response = lambda body: {"product_ids": body["product_ids"]}
        self.fail_with: int | None = None
        mock.post(UPDATE).mock(side_effect=lambda r: self._write(UPDATE, r, self.update_response))
        mock.post(DEACTIVATE).mock(side_effect=lambda r: self._write(DEACTIVATE, r, self.deactivate_response))

    def _write(self, path: str, request: httpx.Request, answer) -> httpx.Response:
        body = json.loads(request.content)
        self.writes.append((path, body))
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"code": self.fail_with, "message": "internal error"})
        return httpx.Response(200, json=answer(body))


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield WritableOzon(mock)


async def ready(sessions, client, tokens, ozon) -> dict:
    """Пользователь после синхронизации; у 1001 и 1003 есть себестоимость (164 ₽, НДС 22 %) и продажи — логистика
    30 ₽ на штуку. Ozon: цена 540 ₽, комиссия 38 %, эквайринг 1,5 %."""
    user_id = await api_user(sessions, client, tokens)
    for pid in (1001, 1003):
        await client.put(f"/api/v1/costs/{OFFERS[pid]}", json={"cost_price": "164", "vat_multiplier": "1.22"}, headers=auth(tokens))
    async with sessions() as db:
        products = {p.ozon_product_id: p for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()}
        await add_sales(db, user_id, products[1001], today_msk() - timedelta(days=1), logistics=Decimal("30"))
        await db.commit()
        promos = {p.ozon_action_id: p.id for p in (await db.execute(select(Promotion).where(Promotion.user_id == user_id))).scalars()}
    return {"user_id": user_id, "products": {k: p.id for k, p in products.items()}, "promos": promos}


def url(env, action_id, pid, what):
    return f"/api/v1/promotions/{env['promos'][action_id]}/products/{env['products'][pid]}/{what}"


async def post(client, tokens, path, **body):
    return await client.post(path, json=body, headers={**auth(tokens), "user-agent": "pytest-browser"})


async def audit_rows(sessions, user_id) -> list[AuditLog]:
    """Записи о действиях в акциях; записи запросов к API (middleware, шаг 6.1) — отдельно, в tests/test_audit.py."""
    async with sessions() as db:
        return list((await db.execute(select(AuditLog).where(
            AuditLog.user_id == user_id, AuditLog.entity_type == "promotion_product",
        ).order_by(AuditLog.created_at))).scalars())


async def current(sessions, env, action_id, pid) -> list[PromotionProduct]:
    async with sessions() as db:
        return list((await db.execute(select(PromotionProduct).where(
            PromotionProduct.promotion_id == env["promos"][action_id], PromotionProduct.product_id == env["products"][pid],
            PromotionProduct.left_at.is_(None),
        ))).scalars())


async def test_join_whole_stock_preview_then_confirm(sessions, client, tokens, ozon):
    env = await ready(sessions, client, tokens, ozon)
    path = url(env, 71001, 1003, "join")  # «Скидка на сток»: участвует весь остаток; кандидат, максимум 430 ₽
    r = await post(client, tokens, path)
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["action"], p["applied"], p["can_apply"], p["errors"]) == ("join", False, True, [])
    # по умолчанию — максимальная цена для участия; количество не указывается — участвует весь остаток
    assert (p["action_price"], p["max_action_price"], p["stock"], p["stock_required"], p["whole_stock"]) == (
        "430.00", "430.00", None, False, True)
    assert any("весь остаток" in w for w in p["warnings"]) and p["whole_stock_total"]["qty"] == 0
    # маржа калькулятором: 430 × 0,605 − (200,08 + логистика 30 — средняя по магазину) = 30,07 ₽ → 6,99 %
    assert (p["unit_profit"], p["margin"], p["discount_pct"]) == ("30.07", "6.99", "20.4")
    assert p["before"]["participating"] is False and p["after"] == {"participating": True, "action_price": "430.00", "stock": None}
    assert ozon.writes == []  # предпросмотр в Ozon не ходит

    r = await post(client, tokens, path, confirm=True)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True and r.json()["message"].startswith("Товар добавлен в акцию")
    assert ozon.writes == [(UPDATE, {"action_id": 71001, "products": [
        {"product_id": 1003, "action_price": {"amount": "430.00", "currency": "RUB"}}]})]
    rows = await current(sessions, env, 71001, 1003)
    assert len(rows) == 1 and rows[0].is_active and rows[0].action_price == Decimal("430.00")  # кандидат стал участником
    async with sessions() as db:
        snaps = (await db.execute(select(PromotionPriceSnapshot).where(PromotionPriceSnapshot.promotion_product_id == rows[0].id))).scalars().all()
    assert [s.action_price for s in snaps] == [Decimal("430.00")]
    log = await audit_rows(sessions, env["user_id"])
    assert len(log) == 1 and (log[0].action, log[0].status, log[0].entity_id) == ("promotion.join", "applied", "71001:1003")
    assert log[0].ozon_request["path"] == UPDATE and log[0].ozon_response["active_product_ids"] == ["1003"]
    assert log[0].before_json["participating"] is False and log[0].after_json["action_price"] == "430.00"
    assert log[0].user_agent == "pytest-browser"

    # та же пара — не чаще раза в час: защита, отказ тоже в журнале
    r = await post(client, tokens, path, action_price="425", confirm=True)
    assert r.status_code == 400 and "не раньше чем через час" in r.json()["detail"]
    assert len(ozon.writes) == 1 and (await audit_rows(sessions, env["user_id"]))[-1].status == "rejected"


async def test_join_checks(sessions, client, tokens, ozon):
    env = await ready(sessions, client, tokens, ozon)
    path = url(env, 71003, 1001, "join")  # «Осенняя распродажа»: обычная акция, кандидат по 432 ₽ без максимума
    p = (await post(client, tokens, path)).json()
    assert (p["action_price"], p["stock"], p["stock_required"]) == ("432.00", 1, True)
    assert any("на складах сейчас 0" in w for w in p["warnings"])
    errors = lambda **body: post(client, tokens, path, **body)  # noqa: E731
    assert any("ниже обычной цены" in e for e in (await errors(action_price="600")).json()["errors"])
    # себестоимость 164 × 1,22 × 1,05 = 210,08 — ниже нельзя
    assert any("210,08" in e for e in (await errors(action_price="200")).json()["errors"])
    # 300 ₽: 300 × 0,605 − 230,08 = −48,58 ₽ — в минус: без «понимаю» не применяется
    p = (await errors(action_price="300")).json()
    assert p["can_apply"] and p["needs_loss_ack"] and any("в минус: 48,58" in w for w in p["warnings"])
    r = await post(client, tokens, path, action_price="300", confirm=True)
    assert r.status_code == 400 and "подтвердите" in r.json()["detail"] and ozon.writes == []
    r = await post(client, tokens, path, action_price="300", stock=3, accept_loss=True, confirm=True)
    assert r.status_code == 200 and r.json()["applied"]
    assert ozon.writes[-1][1]["products"] == [
        {"product_id": 1001, "action_price": {"amount": "300.00", "currency": "RUB"}, "stock": "3"}]
    # нет себестоимости (1002) — нельзя: не проверить нижнюю границу цены
    p = (await post(client, tokens, url(env, 71003, 1002, "join"))).json()
    assert not p["can_apply"] and any("Нет себестоимости" in e for e in p["errors"])
    # не кандидат и заморожено («Эластичный бустинг» заморожен 10.10.2026)
    p = (await post(client, tokens, url(env, 71002, 1001, "join"))).json()
    assert any("нет среди кандидатов" in e for e in p["errors"]) and any("заморожена" in e for e in p["errors"])


async def test_ozon_rejects_or_fails(sessions, client, tokens, ozon):
    env = await ready(sessions, client, tokens, ozon)
    path = url(env, 71001, 1003, "join")
    ozon.update_response = lambda body: {"active_product_ids": [], "rejected": [{"product_id": 1003, "reason": "PRICE_TOO_HIGH"}]}
    r = await post(client, tokens, path, confirm=True)
    assert r.status_code == 409 and r.json()["detail"] == "Ozon не добавил товар в акцию: PRICE_TOO_HIGH."
    rows = await current(sessions, env, 71001, 1003)
    assert len(rows) == 1 and not rows[0].is_active  # всё ещё кандидат — ничего не изменилось
    ozon.fail_with = 500
    r = await post(client, tokens, path, confirm=True)
    assert r.status_code == 502 and "ничего не изменилось" in r.json()["detail"]
    statuses = [(a.status, a.ozon_request is not None) for a in await audit_rows(sessions, env["user_id"])]
    assert statuses == [("rejected", True), ("failed", True)]


async def test_leave(sessions, client, tokens, ozon):
    env = await ready(sessions, client, tokens, ozon)
    path = url(env, 71001, 1001, "leave")
    p = (await post(client, tokens, path)).json()
    assert (p["action"], p["can_apply"], p["before"]["participating"], p["after"]["participating"]) == ("leave", True, True, False)
    assert ozon.writes == []
    r = await post(client, tokens, path, confirm=True)
    assert r.status_code == 200 and r.json()["message"] == "Товар убран из акции."
    assert ozon.writes == [(DEACTIVATE, {"action_id": 71001, "product_ids": ["1001"]})]
    assert await current(sessions, env, 71001, 1001) == []  # больше не участвует — строка с датой выхода осталась
    history = (await client.get("/api/v1/promotions/history", params={"product_id": str(env["products"][1001])},
                                headers=auth(tokens))).json()["items"]
    assert [i["status"] for i in history] == ["left"]
    log = (await audit_rows(sessions, env["user_id"]))[-1]
    assert (log.action, log.status, log.after_json["participating"]) == ("promotion.leave", "applied", False)
    # уже не участвует — второй раз нельзя
    assert "не участвует" in (await post(client, tokens, path, confirm=True)).json()["detail"]
    # «Эластичный бустинг»: выход — через цену в кабинете Ozon, не выходом; к тому же заморожен
    r = await post(client, tokens, url(env, 71002, 1003, "leave"), confirm=True)
    assert r.status_code == 400 and "через цену в акции" in r.json()["detail"] and "заморожена" in r.json()["detail"]
    assert len(ozon.writes) == 1
    # Ozon не убрал
    ozon.deactivate_response = lambda body: {"product_ids": []}
    r = await post(client, tokens, url(env, 71001, 1002, "leave"), confirm=True)
    assert r.status_code == 409 and r.json()["detail"].startswith("Ozon не убрал товар из акции")


async def test_access(sessions, client, tokens, ozon):
    env = await ready(sessions, client, tokens, ozon)
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    r = await client.post(url(env, 71001, 1003, "join"), json={"confirm": True}, headers=auth(t2))
    assert r.status_code == 404 and r.json()["detail"] == "Акция не найдена"
    assert (await client.post(url(env, 71001, 1003, "join"), json={})).status_code == 401
    bad = await post(client, tokens, url(env, 71001, 1003, "join"), action_price="-5")
    assert bad.status_code == 400
    assert ozon.writes == []


async def test_summary_and_single_promotion(sessions, client, tokens, ozon):
    env = await ready(sessions, client, tokens, ozon)
    data = (await client.get("/api/v1/promotions", headers=auth(tokens))).json()
    stock = next(p for p in data if p["ozon_action_id"] == 71001)
    # 1001: 459 × 0,605 − (200,08 + 30) = 47,615 ₽ → 10,37 % — выгодный; 1002 без себестоимости — маржи нет
    assert (stock["avg_margin"], stock["profitable"], stock["low_margin"], stock["loss"], stock["no_margin"]) == (
        "10.37", 1, 0, 0, 1)
    assert stock["urgent"] is False
    one = (await client.get(f"/api/v1/promotions/{env['promos'][71001]}", headers=auth(tokens))).json()
    assert one["title"] == "Скидка на сток" and one["avg_margin"] == "10.37"
    # кандидат — маржа при цене входа по умолчанию (максимальная 430 ₽): 6,99 %
    rows = (await client.get(f"/api/v1/promotions/{env['promos'][71001]}/products", headers=auth(tokens))).json()["items"]
    cand = next(r for r in rows if not r["is_active"])
    assert cand["margin_in_promo"] == "6.99" and cand["margin_level"] == "low"
    other = await client.get(f"/api/v1/promotions/{env['promos'][71003]}", headers=auth(tokens))
    assert other.status_code == 200 and other.json()["products_active"] == 0
    assert (await client.get(f"/api/v1/promotions/{uuid.uuid4()}", headers=auth(tokens))).status_code == 404


def test_urgent_freeze():
    now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)  # 12:00 по Москве
    assert is_urgent(datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc), now)  # завтра 23:00 МСК
    assert is_urgent(datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc), now)  # сегодня, ещё не наступила
    assert not is_urgent(datetime(2026, 10, 17, 21, 30, tzinfo=timezone.utc), now)  # послезавтра 00:30 МСК
    assert not is_urgent(datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc), now)  # уже наступила
    assert not is_urgent(None, now)
