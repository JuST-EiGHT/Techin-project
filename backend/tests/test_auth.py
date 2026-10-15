from app.services.security import create_access_token, decode_token, hash_password, verify_password
from tests.conftest import USER

API = "/api/v1/auth"


def auth(t):
    return {"Authorization": f"Bearer {t}"}


# ── health ──
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


# ── security primitives ──
def test_password_hashing():
    h = hash_password("secret123")
    assert h != "secret123" and h.startswith("$2")
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_token_type_enforced():
    import uuid

    import pytest

    from app.services.security import InvalidTokenError

    t = create_access_token(uuid.uuid4())
    assert decode_token(t, "access")["type"] == "access"
    with pytest.raises(InvalidTokenError):
        decode_token(t, "refresh")


# ── register ──
async def test_register_ok(client):
    r = await client.post(f"{API}/register", json=USER)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "seller@example.com"  # email нормализуется
    assert body["full_name"] == "Радимир"
    assert body["is_active"] is True and body["is_verified"] is False
    assert "password" not in body and "hashed_password" not in body


async def test_register_duplicate_400(client):
    await client.post(f"{API}/register", json=USER)
    r = await client.post(f"{API}/register", json={**USER, "email": "seller@example.com"})
    assert r.status_code == 400
    assert "уже зарегистрирован" in r.json()["detail"]


async def test_register_validation_400(client):
    r = await client.post(f"{API}/register", json={"email": "not-email", "password": "short"})
    assert r.status_code == 400
    assert len(r.json()["errors"]) == 2


# ── login ──
async def test_login_ok(tokens):
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == 24 * 3600


async def test_login_wrong_password_401(client):
    await client.post(f"{API}/register", json=USER)
    r = await client.post(f"{API}/login", json={"email": USER["email"], "password": "wrongpass"})
    assert r.status_code == 401


async def test_login_unknown_user_401(client):
    r = await client.post(f"{API}/login", json={"email": "nobody@x.ru", "password": "whatever1"})
    assert r.status_code == 401


# ── me ──
async def test_me_ok(client, tokens):
    r = await client.get(f"{API}/me", headers=auth(tokens["access_token"]))
    assert r.status_code == 200 and r.json()["email"] == "seller@example.com"


async def test_me_without_token_401(client):
    r = await client.get(f"{API}/me")
    assert r.status_code == 401


async def test_me_with_refresh_token_401(client, tokens):
    r = await client.get(f"{API}/me", headers=auth(tokens["refresh_token"]))
    assert r.status_code == 401


async def test_me_garbage_token_401(client):
    r = await client.get(f"{API}/me", headers=auth("abc.def.ghi"))
    assert r.status_code == 401


# ── refresh ──
async def test_refresh_issues_new_access(client, tokens):
    r = await client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["access_token"] != tokens["access_token"]
    assert (await client.get(f"{API}/me", headers=auth(new["access_token"]))).status_code == 200


async def test_refresh_rotation_old_token_rejected(client, tokens):
    await client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
    r = await client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_with_access_token_401(client, tokens):
    r = await client.post(f"{API}/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401


# ── logout ──
async def test_logout_revokes_tokens(client, tokens):
    h = auth(tokens["access_token"])
    r = await client.post(f"{API}/logout", json={"refresh_token": tokens["refresh_token"]}, headers=h)
    assert r.status_code == 204
    assert (await client.get(f"{API}/me", headers=h)).status_code == 401
    r = await client.post(f"{API}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_logout_requires_auth(client):
    r = await client.post(f"{API}/logout")
    assert r.status_code == 401


# ── deactivated user → 403 ──
async def test_deactivated_user_403(client, tokens):
    import uuid

    from app.crud.user import deactivate_user, get_user_by_id
    from app.db.session import get_db
    from app.main import app
    from app.services.security import decode_token

    uid = uuid.UUID(decode_token(tokens["access_token"])["sub"])
    async for db in app.dependency_overrides[get_db]():
        await deactivate_user(db, await get_user_by_id(db, uid))
    assert (await client.get(f"{API}/me", headers=auth(tokens["access_token"]))).status_code == 403
    r = await client.post(f"{API}/login", json={"email": USER["email"], "password": USER["password"]})
    assert r.status_code == 403


# ── CORS ──
async def test_cors_allowed_origin(client):
    r = await client.options(
        f"{API}/login",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


async def test_cors_foreign_origin_blocked(client):
    r = await client.options(
        f"{API}/login",
        headers={"Origin": "http://evil.com", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
