"""История изменений цен и откат (шаг 6.4): откат — тот же предпросмотр → применение по preview_id, без паузы и лимита
20 % (решение владельца), только ровно к прежней цене, не старше 7 дней, без отката отката; GET /prices/history."""
import uuid
from datetime import timedelta
from decimal import Decimal as D

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.price_change import APPLIED, MANUAL_SINGLE, PriceChange
from tests.test_price_updater import OFFER, apply, journal, latest_price, ozon, preview, ready  # noqa: F401
from tests.test_pricing import auth

OTHER = "6000-2RS.C3.SRL.NAC.JP"


async def change(client, tokens, env, price, pid=1001) -> str:
    """Изменение цены через предпросмотр и применение — id попытки."""
    p = (await preview(client, tokens, env, price, pid=pid)).json()
    assert p["can_apply"], p["errors"]
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 200, r.text
    return r.json()["change_id"]


async def rollback(client, tokens, change_id):
    return await client.post(f"/api/v1/prices/rollback/{change_id}", headers={**auth(tokens), "user-agent": "pytest-browser"})


async def test_rollback_without_pause_and_20_pct(client, tokens, sessions, ozon):  # noqa: F811
    env = await ready(client, tokens, sessions)
    original = await change(client, tokens, env, "400", pid=1003)  # 499 → 400 (−19,8 %)
    r = await rollback(client, tokens, original)
    assert r.status_code == 200, r.text
    p = r.json()
    # обратно — +24,8 %, и пауза час ещё идёт: для отката можно (решение владельца)
    assert (p["can_apply"], p["errors"]) == (True, [])
    assert (p["price_before"], p["price_after"], p["change_pct"]) == ("400.00", "499.00", "24.8")
    assert p["rollback_of"]["id"] == original and p["rollback_of"]["price_before"] == "499.00"
    assert p["reason"].startswith("Откат изменения от ")
    assert len(ozon.imports) == 1  # предпросмотр отката в Ozon не ходит
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 200, r.text
    assert r.json()["message"].startswith("Цена возвращена: 499,00 ₽ отправлена в Ozon")
    assert ozon.imports[-1] == {"prices": [{"product_id": 1003, "price": "499.00", "currency_code": "RUB"}]}
    async with sessions() as db:
        undo = await db.get(PriceChange, uuid.UUID(r.json()["change_id"]))
    assert (undo.status, str(undo.rollback_of_id), undo.change_type) == (APPLIED, original, MANUAL_SINGLE)
    entry = (await journal(sessions, env["user_id"]))[-1]
    assert (entry.action, entry.status, entry.entity_id) == ("price.rollback", "applied", str(undo.id))
    log = (await client.get("/api/v1/audit/prices", params={"kind": "price"}, headers=auth(tokens))).json()
    assert [i["action_title"] for i in log["items"]] == ["Откат цены", "Изменение цены"]
    assert not [a for a in await journal(sessions, env["user_id"], "http") if "/rollback/" in a.path]
    assert (await latest_price(sessions, env["products"][1003])).price == D("499")

    # второй раз — уже откатили; откат отката — нельзя
    p = (await rollback(client, tokens, original)).json()
    assert not p["can_apply"] and any("уже откатили" in e for e in p["errors"])
    p = (await rollback(client, tokens, str(undo.id))).json()
    assert not p["can_apply"] and any("откат отката запрещён" in e for e in p["errors"])


async def test_rollback_blocks(client, tokens, sessions, ozon):  # noqa: F811
    env = await ready(client, tokens, sessions)
    original = await change(client, tokens, env, "600")
    ozon.items[1001]["price"] = "610"  # цену поменяли в кабинете Ozon — откат вернул бы не ту
    p = (await rollback(client, tokens, original)).json()
    assert any("сейчас 610,00 ₽, а изменение ставило 600,00 ₽" in e for e in p["errors"])
    r = await apply(client, tokens, p["preview_id"])
    assert r.status_code == 400 and len(ozon.imports) == 1

    async with sessions() as db:  # изменение 8 дней назад и заблокированная попытка
        old = PriceChange(user_id=env["user_id"], product_id=env["products"][1003], offer_id=OTHER,
                          created_at=(await db.get(PriceChange, uuid.UUID(original))).created_at - timedelta(days=8),
                          change_type=MANUAL_SINGLE, price_before=D("520"), price_after=D("499"), status=APPLIED,
                          is_applied=True)
        blocked = PriceChange(user_id=env["user_id"], product_id=env["products"][1003], offer_id=OTHER,
                              created_at=old.created_at, change_type=MANUAL_SINGLE, price_before=D("499"),
                              price_after=D("700"), status="blocked", errors=["Изменение на +40,3 %"])
        db.add_all([old, blocked])
        await db.commit()
    p = (await rollback(client, tokens, old.id)).json()
    assert any("не старше 7 дней" in e for e in p["errors"])
    p = (await rollback(client, tokens, blocked.id)).json()
    assert any("не дошло до Ozon" in e for e in p["errors"])

    # предпросмотр, чужое и несуществующее изменение — не найдено
    pv = (await preview(client, tokens, env, "560", pid=1003)).json()
    assert (await rollback(client, tokens, pv["preview_id"])).status_code == 404
    assert (await rollback(client, tokens, uuid.uuid4())).status_code == 404
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    r = await rollback(client, t2, original)
    assert r.status_code == 404 and r.json()["detail"] == "Изменение цены не найдено."


async def test_history(client, tokens, sessions, ozon):  # noqa: F811
    env = await ready(client, tokens, sessions)
    first = await change(client, tokens, env, "600")  # 1001: 540 → 600
    p = (await preview(client, tokens, env, "700", pid=1003)).json()  # +40 % — заблокировано
    assert (await apply(client, tokens, p["preview_id"])).status_code == 400  # предпросмотр с ошибками — не попытка
    ozon.items[1003]["price"] = "499"
    second = await change(client, tokens, env, "520", pid=1003)  # 1003: 499 → 520
    ozon.items[1003]["price"] = "560"  # потом цену поменяли в кабинете — откатить нельзя
    await preview(client, tokens, env, "570", pid=1003)  # сервис узнал новую цену (так же — синхронизация товаров)
    bulk = (await client.post("/api/v1/prices/bulk/preview", headers=auth(tokens), json={
        "strategy": {"kind": "percent", "value": "5"}, "product_ids": [str(env["products"][1002])]})).json()
    assert bulk["counts"]["blocked"] == 1  # без себестоимости — в применение не попадёт
    undo = (await rollback(client, tokens, first)).json()
    undo_id = (await apply(client, tokens, undo["preview_id"])).json()["change_id"]

    r = await client.get("/api/v1/prices/history", headers=auth(tokens))
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 3 and page["counts"]["applied"] == 3 and page["counts"]["all"] == 3  # предпросмотры не входят
    items = {i["id"]: i for i in page["items"]}
    assert [i["id"] for i in page["items"]] == [undo_id, second, first]  # новые первыми
    rb = items[undo_id]
    assert (rb["kind"], rb["kind_title"], rb["rollback_of"]["id"], rb["can_rollback"], rb["rollback_block"]) == (
        "rollback", "Откат", first, False, "это откат — откат отката запрещён")
    assert (rb["price_before"], rb["price_after"], rb["change_pct"]) == ("600.00", "540.00", "-10.0")
    one = items[first]
    assert one["rolled_back_by"]["id"] == undo_id and one["rollback_block"] == "уже откатили"
    assert (one["kind"], one["status_title"], one["change_pct"]) == ("single", "Применено", "11.1")
    assert items[second]["rollback_block"] == "цена с тех пор изменилась"

    async def ids(**params):
        r = await client.get("/api/v1/prices/history", params=params, headers=auth(tokens))
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["items"]]

    assert await ids(kind="rollback") == [undo_id] and await ids(kind="single") == [second, first]
    assert await ids(product_id=str(env["products"][1003])) == [second]
    assert await ids(search="6000-2rs") == [second] and await ids(search="подшипник 6001") == [undo_id, first]
    assert await ids(status="not_applied") == [] and await ids(status="applied") == [undo_id, second, first]
    assert await ids(date_from="2026-01-01", date_to="2026-01-31") == []
    assert await ids(limit=1, offset=1) == [second]


async def test_history_statuses_and_access(client, tokens, sessions, ozon):  # noqa: F811
    env = await ready(client, tokens, sessions)
    ozon.import_answer = lambda body: {"result": [{"product_id": 1001, "updated": False, "errors": [
        {"code": "price_out_of_range", "message": "x"}]}]}
    p = (await preview(client, tokens, env, "600")).json()
    assert (await apply(client, tokens, p["preview_id"])).status_code == 409
    ozon.import_answer = lambda body: {"result": [{"product_id": 1003, "updated": True, "errors": []}]}
    fresh = await change(client, tokens, env, "520", pid=1003)
    page = (await client.get("/api/v1/prices/history", headers=auth(tokens))).json()
    items = {i["id"]: i for i in page["items"]}
    rejected = next(i for i in page["items"] if i["status"] == "rejected")
    assert rejected["status_title"] == "Ozon отказал" and "порог категории" in rejected["error"]
    assert rejected["rollback_block"] == "не дошло до Ozon"
    assert items[fresh]["can_rollback"] is True and items[fresh]["rollback_block"] is None
    assert page["counts"]["not_applied"] == 1 and page["counts"]["rejected"] == 1

    bad = await client.get("/api/v1/prices/history", params={"date_from": "2026-10-10", "date_to": "2026-10-01"},
                           headers=auth(tokens))
    assert bad.status_code == 400 and "позже" in bad.json()["detail"]
    assert (await client.get("/api/v1/prices/history", params={"status": "preview"}, headers=auth(tokens))).status_code == 400
    assert (await client.get("/api/v1/prices/history")).status_code == 401
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    assert (await client.get("/api/v1/prices/history", headers=auth(t2))).json()["total"] == 0
    r = await client.get("/api/v1/prices/history", params={"product_id": str(env["products"][1001])}, headers=auth(t2))
    assert r.status_code == 404
    async with sessions() as db:
        assert await db.scalar(select(AuditLog.id).where(AuditLog.action == "price.update")) is not None
