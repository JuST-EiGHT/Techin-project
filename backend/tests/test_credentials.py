import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import settings
from app.crud.credential import add_months
from app.models.api_credential import ApiCredential
from app.services import encryption

API = "/api/v1/credentials"
CLIENT_ID = "1234567"
API_KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"
NEW = {"label": "Основной", "client_id": CLIENT_ID, "api_key": API_KEY}


def auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


@pytest.fixture
def ozon():
    """Мок Ozon: по умолчанию ключ принимается."""
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        mock.post("/v3/product/list", name="product_list").respond(200, json={"result": {"items": [], "total": 0, "last_id": ""}})
        yield mock


def set_ozon(mock, *, status=200, body=None, exc=None):
    route = mock.routes["product_list"]
    if exc:
        route.side_effect = exc
    else:
        route.side_effect = None
        route.return_value = httpx.Response(status, json=body or {})


async def add(client, tokens, **over):
    return await client.post(API, json={**NEW, **over}, headers=auth(tokens))


async def other_user_tokens(client):
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    return (await client.post("/api/v1/auth/login", json=other)).json()


# ── шифрование ──
def test_encrypt_roundtrip_and_random():
    a, b = encryption.encrypt(API_KEY), encryption.encrypt(API_KEY)
    assert a != b and API_KEY not in a
    assert encryption.decrypt(a) == API_KEY


@pytest.mark.parametrize("key", ["", "change_me", "not-a-fernet-key"])
def test_bad_encryption_key_rejected(key):
    with pytest.raises(encryption.EncryptionKeyError) as e:
        encryption.init_encryption(key)
    assert "ENCRYPTION_KEY" in str(e.value) and "Fernet.generate_key" in str(e.value)


def test_decrypt_with_other_key_fails(monkeypatch):
    token = encryption.encrypt(API_KEY)
    monkeypatch.setattr(encryption, "_fernet", None)
    encryption.init_encryption("u6ZOD8vb2Wkx2C6dGqv2bJ5O9n0xZb1Hq3rB1yQyN1w=")
    with pytest.raises(encryption.DecryptionError):
        encryption.decrypt(token)


async def test_app_does_not_start_without_key(monkeypatch):
    from app.main import app

    monkeypatch.setattr(encryption, "_fernet", None)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "")
    with pytest.raises(encryption.EncryptionKeyError):
        async with app.router.lifespan_context(app):
            pass


def test_add_months_clamps_day():
    assert add_months(datetime(2026, 8, 31, tzinfo=timezone.utc), 6) == datetime(2027, 2, 28, tzinfo=timezone.utc)
    assert add_months(datetime(2026, 10, 12, tzinfo=timezone.utc), 6) == datetime(2027, 4, 12, tzinfo=timezone.utc)


# ── добавление ──
async def test_add_ok(client, tokens, ozon):
    r = await add(client, tokens)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "Основной"
    assert body["client_id_masked"] == "••••4567"
    assert body["is_active"] is True and body["last_used_at"] is not None
    assert 180 <= body["days_until_expiry"] <= 184  # +6 месяцев
    assert not {"api_key", "api_key_encrypted", "client_id"} & body.keys()

    # в Ozon ушёл правильный тестовый запрос
    req = ozon.routes["product_list"].calls.last.request
    assert req.headers["Client-Id"] == CLIENT_ID and req.headers["Api-Key"] == API_KEY
    assert json.loads(req.content)["limit"] == 1


async def test_key_stored_encrypted(client, tokens, ozon):
    from app.db.session import get_db
    from app.main import app

    await add(client, tokens)
    async for db in app.dependency_overrides[get_db]():
        cred = (await db.execute(select(ApiCredential))).scalar_one()
        assert API_KEY not in cred.api_key_encrypted
        assert encryption.decrypt(cred.api_key_encrypted) == API_KEY
        assert API_KEY not in repr(cred)


async def test_add_custom_label_default_and_expiry(client, tokens, ozon):
    exp = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    body = (await add(client, tokens, label="  ", expires_at=exp)).json()
    assert body["label"] == "Магазин Ozon"
    assert body["days_until_expiry"] == 19


@pytest.mark.parametrize(
    "status,body,code,text",
    [
        (401, {"code": 16, "message": "Invalid Api-Key, please contact support"}, 400, "Неверный Client-Id или Api-Key"),
        (403, {"code": 7, "message": "Api-key is deactivated, use another one"}, 400, "отключён или истёк"),
        (403, {"code": 7, "message": "Access denied"}, 400, "нет прав"),
        (429, {"code": 8, "message": "rate limit"}, 503, "ограничил запросы"),
        (500, {"message": "internal"}, 503, "Ozon недоступен"),
    ],
)
async def test_add_rejected_not_saved(client, tokens, ozon, status, body, code, text):
    set_ozon(ozon, status=status, body=body)
    r = await add(client, tokens)
    assert r.status_code == code
    assert r.json()["code"] == code and text in r.json()["detail"]
    assert (await client.get(API, headers=auth(tokens))).json() == []


async def test_add_ozon_unreachable(client, tokens, ozon):
    set_ozon(ozon, exc=httpx.ConnectTimeout("timeout"))
    r = await add(client, tokens)
    assert r.status_code == 503 and "Ozon недоступен" in r.json()["detail"]


async def test_add_validation(client, tokens, ozon):
    r = await add(client, tokens, client_id="12ab")
    assert r.status_code == 400 and r.json()["detail"] == "Client-Id: только цифры"
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = await add(client, tokens, expires_at=past)
    assert r.status_code == 400 and "в будущем" in r.json()["detail"]
    r = await add(client, tokens, api_key="   ")
    assert r.status_code == 400 and r.json()["detail"].startswith("Api-Key:")
    assert not ozon.routes["product_list"].called


async def test_requires_auth(client):
    assert (await client.get(API)).status_code == 401
    assert (await client.post(API, json=NEW)).status_code == 401


# ── список, удаление, чужие ключи ──
async def test_list_delete_and_isolation(client, tokens, ozon):
    cred_id = (await add(client, tokens)).json()["id"]
    other = await other_user_tokens(client)

    assert len((await client.get(API, headers=auth(tokens))).json()) == 1
    assert (await client.get(API, headers=auth(other))).json() == []
    # чужой ключ — 404, как несуществующий
    assert (await client.post(f"{API}/{cred_id}/verify", headers=auth(other))).status_code == 404
    r = await client.delete(f"{API}/{cred_id}", headers=auth(other))
    assert r.status_code == 404 and r.json()["detail"] == "Ключ не найден"

    assert (await client.delete(f"{API}/{cred_id}", headers=auth(tokens))).status_code == 204
    assert (await client.get(API, headers=auth(tokens))).json() == []
    assert (await client.delete(f"{API}/{cred_id}", headers=auth(tokens))).status_code == 404


# ── повторная проверка ──
async def test_verify_updates_state(client, tokens, ozon):
    cred_id = (await add(client, tokens)).json()["id"]
    url = f"{API}/{cred_id}/verify"

    set_ozon(ozon, status=401, body={"message": "Invalid Api-Key"})
    r = (await client.post(url, headers=auth(tokens))).json()
    assert r["ok"] is False and r["credential"]["is_active"] is False and "Неверный" in r["message"]

    set_ozon(ozon, status=503)  # Ozon лежит — состояние ключа не трогаем
    r = (await client.post(url, headers=auth(tokens))).json()
    assert r["ok"] is False and r["credential"]["is_active"] is False and "недоступен" in r["message"]

    set_ozon(ozon, status=200, body={"result": {}})
    r = (await client.post(url, headers=auth(tokens))).json()
    assert r["ok"] is True and r["message"] == "Ключ работает" and r["credential"]["is_active"] is True
    # проверка ушла с расшифрованным ключом
    assert ozon.routes["product_list"].calls.last.request.headers["Api-Key"] == API_KEY


# ── Api-Key не утекает ни в ответы, ни в логи ──
async def test_api_key_never_returned_or_logged(client, tokens, ozon, caplog):
    caplog.set_level(logging.DEBUG)  # все логгеры, включая httpx/sqlalchemy/наши
    bodies: list[str] = []

    async def call(method, url, **kw):
        r = await client.request(method, url, headers=auth(tokens), **kw)
        bodies.append(r.text)
        return r

    cred_id = (await call("POST", API, json=NEW)).json()["id"]
    await call("GET", API)
    await call("POST", f"{API}/{cred_id}/verify")
    set_ozon(ozon, status=401, body={"message": f"Invalid Api-Key {API_KEY}"})  # даже если Ozon вернёт ключ в ответе
    await call("POST", f"{API}/{cred_id}/verify")
    await call("POST", API, json=NEW)
    set_ozon(ozon, exc=httpx.ConnectError(f"boom {API_KEY}"))
    await call("POST", API, json=NEW)
    await call("POST", API, json={**NEW, "api_key": API_KEY * 10})  # слишком длинный → ошибка валидации
    await call("POST", API, json={**NEW, "client_id": "x", "api_key": API_KEY})
    await call("DELETE", f"{API}/{cred_id}")

    assert len(bodies) == 9
    for b in bodies:
        assert API_KEY not in b
    assert API_KEY not in caplog.text
    assert caplog.records, "логи должны были писаться — иначе тест ничего не проверяет"
    # полный Client-Id тоже не показываем
    assert all(CLIENT_ID not in b for b in bodies)

