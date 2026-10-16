"""Журнал действий (шаг 6.1): middleware пишет изменяющие запросы в audit_log, GET /audit/prices, записи не меняются и
не удаляются (триггер Postgres)."""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import respx
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import audit_middleware
from app.core.config import settings
from app.models.audit import AuditLog
from app.models.user import User
from app.services.sync.finance import today_msk
from tests.conftest import USER
from tests.test_pricing import auth, user_id_of
from tests.test_promotion_actions import ozon, post, ready, url  # noqa: F401 — фикстура фейкового Ozon

SECRET_KEY = "ozon-secret-api-key-0123456789"


async def rows(sessions, user_id=None, entity_type: str | None = "http") -> list[AuditLog]:
    q = select(AuditLog).order_by(AuditLog.created_at)
    if user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    if entity_type is not None:
        q = q.where(AuditLog.entity_type == entity_type)
    async with sessions() as db:
        return list((await db.execute(q)).scalars())


async def test_mutating_requests_are_logged(client, tokens, sessions):
    user_id = await user_id_of(client, tokens)
    h = {**auth(tokens), "user-agent": "pytest-browser", "x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    r = await client.put("/api/v1/costs/ART-1", json={"cost_price": "164", "note": "закупка"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/costs", headers=h)).status_code == 200  # чтение — не пишется
    [row] = await rows(sessions, user_id)
    assert (row.action, row.entity_id, row.method, row.path, row.http_status, row.status) == (
        "http.put", "/api/v1/costs/{offer_id:path}", "PUT", "/api/v1/costs/ART-1", 200, "ok")
    assert (row.ip, row.user_agent) == ("203.0.113.7", "pytest-browser") and row.request_id is not None
    # тело запроса не пишем — там бывают пароли и ключи
    assert (row.before_json, row.after_json, row.ozon_request, row.product_id) == (None, None, None, None)
    # не прошло проверку — тоже запись: rejected и HTTP-статус
    r = await client.patch("/api/v1/settings", json={"theme": "pink"}, headers=h)
    assert r.status_code == 400
    last = (await rows(sessions, user_id))[-1]
    assert (last.action, last.entity_id, last.status, last.http_status) == ("http.patch", "/api/v1/settings", "rejected", 400)
    r = await client.delete(f"/api/v1/reports/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404 and (await rows(sessions, user_id))[-1].action == "http.delete"


async def test_secrets_never_logged(client, tokens, sessions):
    user_id = await user_id_of(client, tokens)
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        mock.post("/v1/roles").respond(401, json={"message": "Invalid Api-Key, please check the key and try again"})
        r = await client.post("/api/v1/credentials", json={"client_id": "12345", "api_key": SECRET_KEY},
                              headers=auth(tokens))
    assert r.status_code == 400
    new_password = "brand-new-pass-77"
    r = await client.post("/api/v1/auth/change-password",
                          json={"current_password": USER["password"], "new_password": new_password}, headers=auth(tokens))
    assert r.status_code == 204
    logged = await rows(sessions, entity_type=None)
    assert [(a.entity_id, a.http_status) for a in logged] == [("/api/v1/credentials", 400), ("/api/v1/auth/change-password", 204)]
    assert all(a.user_id == user_id for a in logged)
    dump = repr([{c.name: getattr(a, c.name) for c in AuditLog.__table__.columns} for a in logged])
    for secret in (SECRET_KEY, USER["password"], new_password):
        assert secret not in dump


async def test_not_logged_without_user_or_change(client, tokens, sessions):
    user_id = await user_id_of(client, tokens)
    # без входа (регистрация, вход) и с недействительным токеном — пользователя нет
    await client.post("/api/v1/auth/register", json={"email": "two@example.com", "password": "strongpass2"})
    await client.post("/api/v1/auth/login", json={"email": "two@example.com", "password": "strongpass2"})
    assert (await client.post("/api/v1/sync/products", headers={"Authorization": "Bearer junk"})).status_code == 401
    # ничего не меняют: расчёт цены, разбор отчёта, предпросмотр налога и импорта (dry_run по умолчанию)
    h = auth(tokens)
    assert (await client.post("/api/v1/price/calculate", json={"product_id": str(uuid.uuid4())}, headers=h)).status_code == 404
    bad = {"file": ("report.xlsx", b"not a report", "application/octet-stream")}
    assert (await client.post("/api/v1/reports/parse", files=bad, headers=h)).status_code == 400
    assert (await client.post("/api/v1/tax-profile/preview", json={"regime": "usn_income"}, headers=h)).status_code in (200, 400)
    csv = {"file": ("costs.csv", "Артикул;Себестоимость\nART-1;164\n".encode(), "text/csv")}
    assert (await client.post("/api/v1/costs/import", files=csv, headers=h)).status_code == 200
    assert (await client.post("/api/v1/costs/import/errors", files=csv, headers=h)).status_code in (200, 400)
    assert await rows(sessions, entity_type=None) == []
    # применить — пишется
    r = await client.post("/api/v1/costs/import", params={"dry_run": "false"}, files=csv, headers=h)
    assert r.status_code == 200 and r.json()["applied"]
    assert [(a.user_id, a.path) for a in await rows(sessions, entity_type=None)] == [(user_id, "/api/v1/costs/import")]


async def test_audit_failure_does_not_break_request(client, tokens, sessions, monkeypatch, caplog):
    def broken(**_):
        raise RuntimeError("db is down")

    monkeypatch.setattr(audit_middleware, "AuditLog", broken)
    with caplog.at_level(logging.WARNING, logger=audit_middleware.__name__):
        r = await client.put("/api/v1/costs/ART-1", json={"cost_price": "164"}, headers=auth(tokens))
    assert r.status_code == 200
    assert "Не удалось записать запрос PUT /api/v1/costs/{offer_id:path}" in caplog.text


async def test_unhandled_error_logged_as_failed(client, tokens, sessions, monkeypatch):
    from app.api.v1 import settings as settings_api

    async def boom(*_args, **_kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(settings_api.svc, "update_settings", boom)
    with pytest.raises(RuntimeError):
        await client.patch("/api/v1/settings", json={"theme": "light"}, headers=auth(tokens))
    [row] = await rows(sessions)
    assert (row.status, row.http_status, row.entity_id) == ("failed", 500, "/api/v1/settings")


async def test_price_log(sessions, client, tokens, ozon):  # noqa: F811
    env = await ready(sessions, client, tokens, ozon)
    path = url(env, 71001, 1003, "join")
    assert (await post(client, tokens, path)).status_code == 200  # предпросмотр — в журнал не пишется
    assert (await post(client, tokens, path, confirm=True)).status_code == 200
    assert (await post(client, tokens, path, action_price="425", confirm=True)).status_code == 400  # пауза час
    logged = await rows(sessions, env["user_id"], entity_type=None)
    promo = [a for a in logged if a.entity_type == "promotion_product"]
    joins = [a for a in logged if a.entity_type == "http" and a.path.endswith("/join")]
    assert len(promo) == 2 and len(joins) == 2  # предпросмотр не записан
    assert [a.request_id for a in promo] == [a.request_id for a in joins]  # действие и запрос связаны
    assert all(a.product_id == env["products"][1003] for a in promo)

    page = (await client.get("/api/v1/audit/prices", headers=auth(tokens))).json()
    assert page["total"] == 2 and page["date_to"] == str(today_msk())  # записи запросов к API сюда не входят
    rejected, applied = page["items"]  # новые первыми
    assert (rejected["action"], rejected["action_title"]) == ("promotion.update", "Цена или количество в акции")
    assert (applied["kind"], applied["action"], applied["action_title"], applied["status"], applied["status_title"]) == (
        "promotion", "promotion.join", "Вход в акцию", "applied", "Применено")
    assert applied["product"]["offer_id"] == "6000-2RS.C3.SRL.NAC.JP" and applied["entity_id"] == "71001:1003"
    assert applied["request"]["method"] == "POST" and applied["request"]["http_status"] == 200
    assert applied["before"]["participating"] is False and applied["after"]["action_price"] == "430.00"
    assert applied["ozon_request"]["path"] == "/v1/actions/products/update" and applied["user_agent"] == "pytest-browser"
    assert rejected["status"] == "rejected" and "не раньше чем через час" in rejected["error"]
    assert rejected["request"]["http_status"] == 400 and rejected["ozon_request"] is None

    async def total(**params):
        r = await client.get("/api/v1/audit/prices", params=params, headers=auth(tokens))
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert await total(status="applied") == 1
    assert await total(kind="promotion") == 2 and await total(kind="price") == 0
    # вторая попытка — уже смена цены участника (promotion.update)
    assert await total(action="promotion.join") == 1 and await total(action="promotion.update") == 1
    assert await total(product_id=str(env["products"][1003])) == 2
    assert await total(product_id=str(env["products"][1001])) == 0
    yesterday = today_msk() - timedelta(days=1)
    assert await total(date_from=str(yesterday - timedelta(days=5)), date_to=str(yesterday)) == 0
    one = (await client.get("/api/v1/audit/prices", params={"limit": 1, "offset": 1}, headers=auth(tokens))).json()
    assert one["total"] == 2 and [i["status"] for i in one["items"]] == ["applied"]

    bad = await client.get("/api/v1/audit/prices", params={"date_from": "2026-10-10", "date_to": "2026-10-01"},
                           headers=auth(tokens))
    assert bad.status_code == 400 and "позже" in bad.json()["detail"]
    bad = await client.get("/api/v1/audit/prices", params={"date_from": "2025-01-01", "date_to": "2026-10-01"},
                           headers=auth(tokens))
    assert bad.status_code == 400 and "366" in bad.json()["detail"]
    assert (await client.get("/api/v1/audit/prices", params={"status": "ok"}, headers=auth(tokens))).status_code == 400
    assert (await client.get("/api/v1/audit/prices")).status_code == 401
    # чужой журнал не виден
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    assert (await client.get("/api/v1/audit/prices", headers=auth(t2))).json()["total"] == 0
    r = await client.get("/api/v1/audit/prices", params={"product_id": str(env["products"][1003])}, headers=auth(t2))
    assert r.status_code == 404 and r.json()["detail"] == "Товар не найден"


PG_URL = os.environ.get("TEST_POSTGRES_URL")  # postgresql+asyncpg://… — база после alembic upgrade head


@pytest.mark.skipif(not PG_URL, reason="нужен Postgres с миграциями: TEST_POSTGRES_URL")
async def test_audit_log_is_immutable_in_postgres():
    engine = create_async_engine(PG_URL)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            uid = uuid.uuid4()
            await conn.execute(User.__table__.insert().values(id=uid, email=f"{uid}@example.com", hashed_password="x"))
            await conn.execute(AuditLog.__table__.insert().values(
                id=uuid.uuid4(), user_id=uid, created_at=datetime.now(timezone.utc), action="http.post",
                entity_type="http", entity_id="/api/v1/x", status="ok", after_json={"a": 1},
            ))
            for statement in (
                update(AuditLog).where(AuditLog.user_id == uid).values(status="applied"),
                delete(AuditLog).where(AuditLog.user_id == uid),
                delete(User).where(User.id == uid),  # удаление аккаунта каскадом тоже упирается в журнал
            ):
                savepoint = await conn.begin_nested()
                with pytest.raises(DBAPIError, match="не меняются и не удаляются"):
                    await conn.execute(statement)
                await savepoint.rollback()
            # данные до и после — JSONB: можно искать внутри
            assert await conn.scalar(text("SELECT count(*) FROM audit_log WHERE after_json @> '{\"a\": 1}'")) >= 1
            # удалить можно только намеренно, в той же транзакции
            await conn.execute(text("SET LOCAL app.audit_log_allow_delete = 'on'"))
            assert (await conn.execute(delete(User).where(User.id == uid))).rowcount == 1
            await outer.rollback()
    finally:
        await engine.dispose()
