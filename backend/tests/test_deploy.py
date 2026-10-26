"""Шаг 8.4: выход в интернет — режимы регистрации и приглашения, команды администратора, лимит попыток входа,
закрытая документация API и проверка настроек в проде, /health, Sentry без секретов."""
import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app import admin
from app.core.config import settings
from app.core.monitoring import scrub
from app.models.audit import AuditLog
from app.models.invite import Invite
from app.models.user import User
from app.models.user_session import UserSession
from app.services import registration
from tests.conftest import USER

API = "/api/v1/auth"
NEW = {"email": "friend@example.com", "password": "friendpass1", "full_name": "Друг"}


def mode(monkeypatch, value: str) -> None:
    monkeypatch.setattr(settings, "REGISTRATION_MODE", value)


async def run_admin(sessions, *argv, ask=None) -> tuple[int, str]:
    lines: list[str] = []
    code = await admin.run(list(argv), sessions, out=lines.append, ask=ask or (lambda _p: ""))
    return code, "\n".join(lines)


async def login(client, email, password, ip="203.0.113.5"):
    return await client.post(f"{API}/login", json={"email": email, "password": password},
                             headers={"X-Forwarded-For": ip})


# ── режимы регистрации ──
async def test_registration_mode_endpoint(client, monkeypatch):
    for value in ("closed", "invite", "open"):
        mode(monkeypatch, value)
        r = await client.get(f"{API}/registration")
        assert r.status_code == 200 and r.json() == {"mode": value}


async def test_closed_mode_rejects_registration(client, monkeypatch, sessions):
    mode(monkeypatch, "closed")
    r = await client.post(f"{API}/register", json=NEW)
    assert r.status_code == 403
    assert r.json() == {"detail": registration.CLOSED_MESSAGE, "code": 403}
    async with sessions() as db:
        assert (await db.scalars(select(User))).all() == []


def test_closed_is_the_default_on_server(monkeypatch):
    # не задано: на сервере (ENVIRONMENT=production ставит docker-compose.prod.yml) — закрыто, при разработке — открыто
    monkeypatch.setattr(settings, "REGISTRATION_MODE", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert settings.registration_mode == "closed"
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    assert settings.registration_mode == "open"
    monkeypatch.setattr(settings, "REGISTRATION_MODE", "invite")
    assert settings.registration_mode == "invite"


async def test_open_mode_registers_without_code(client, monkeypatch):
    mode(monkeypatch, "open")
    assert (await client.post(f"{API}/register", json=NEW)).status_code == 201


async def test_invite_mode_requires_valid_code(client, monkeypatch, sessions):
    mode(monkeypatch, "invite")
    r = await client.post(f"{API}/register", json=NEW)
    assert r.status_code == 400 and "по приглашению" in r.json()["detail"]
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": "AAAA-BBBB-CCCC-DDDD"})
    assert r.status_code == 400 and "не найден" in r.json()["detail"]

    async with sessions() as db:
        invite, code = await registration.create_invite(db, note="Друг")
    # регистр, пробелы и дефисы в коде не важны
    messy = " " + code.lower().replace("-", " ") + " "
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": messy})
    assert r.status_code == 201, r.text
    async with sessions() as db:
        used = await db.get(Invite, invite.id)
        user = await db.scalar(select(User).where(User.email == NEW["email"]))
        assert used.used_at is not None and used.used_by == user.id
        assert registration.state(used) == registration.USED
    # одноразовое
    r = await client.post(f"{API}/register", json={**NEW, "email": "other@example.com", "invite_code": code})
    assert r.status_code == 400 and "уже использовано" in r.json()["detail"]


async def test_invite_code_stored_only_as_hash(sessions):
    async with sessions() as db:
        invite, code = await registration.create_invite(db)
        row = await db.get(Invite, invite.id)
    assert len(code) == 19 and code.count("-") == 3
    assert registration.normalize(code) not in (row.code_hash, row.note or "")
    assert row.code_hash == registration.code_hash(code) and row.code_tail == registration.normalize(code)[-4:]


async def test_invite_expired_revoked_and_bound_email(client, monkeypatch, sessions):
    mode(monkeypatch, "invite")
    async with sessions() as db:
        expired, expired_code = await registration.create_invite(db)
        expired.expires_at = registration.utcnow() - timedelta(minutes=1)
        revoked, revoked_code = await registration.create_invite(db)
        _, bound_code = await registration.create_invite(db, email="Boss@Example.com")
        await db.commit()
        await registration.revoke(db, revoked.id)

    r = await client.post(f"{API}/register", json={**NEW, "invite_code": expired_code})
    assert r.status_code == 400 and "истёк" in r.json()["detail"]
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": revoked_code})
    assert r.status_code == 400 and "отозвано" in r.json()["detail"]
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": bound_code})
    assert r.status_code == 400 and "другой email" in r.json()["detail"]
    r = await client.post(f"{API}/register", json={**NEW, "email": "boss@example.com", "invite_code": bound_code})
    assert r.status_code == 201


async def test_invite_guessing_is_limited(client, monkeypatch):
    mode(monkeypatch, "invite")
    monkeypatch.setattr(settings, "INVITE_MAX_ATTEMPTS_PER_IP", 3)
    headers = {"X-Forwarded-For": "198.51.100.7"}
    for _ in range(3):
        r = await client.post(f"{API}/register", json={**NEW, "invite_code": "WRONG"}, headers=headers)
        assert r.status_code == 400
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": "WRONG"}, headers=headers)
    assert r.status_code == 429 and "кода приглашения" in r.json()["detail"] and "Retry-After" in r.headers
    # другой IP — свой счётчик
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": "WRONG"},
                          headers={"X-Forwarded-For": "198.51.100.8"})
    assert r.status_code == 400


# ── лимит попыток входа ──
async def test_login_limit_per_email_and_ip(client, tokens):
    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        assert (await login(client, USER["email"], "wrong-pass")).status_code == 401
    # сверх лимита — 429 даже с верным паролем: пароль не проверяется
    r = await login(client, USER["email"], USER["password"])
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == 429 and "Слишком много неудачных попыток входа" in body["detail"] and "мин" in body["detail"]
    assert int(r.headers["Retry-After"]) > 0
    # хозяин аккаунта с другого IP входит: подбор с чужого адреса его не блокирует
    assert (await login(client, USER["email"], USER["password"], ip="203.0.113.99")).status_code == 200


async def test_login_success_resets_counter(client, tokens):
    for _ in range(settings.LOGIN_MAX_ATTEMPTS - 1):
        await login(client, USER["email"], "wrong-pass")
    assert (await login(client, USER["email"], USER["password"])).status_code == 200
    for _ in range(settings.LOGIN_MAX_ATTEMPTS - 1):
        assert (await login(client, USER["email"], "wrong-pass")).status_code == 401
    assert (await login(client, USER["email"], USER["password"])).status_code == 200


async def test_login_limit_per_ip_across_emails(client, tokens, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS_PER_IP", 4)
    for i in range(4):
        assert (await login(client, f"user{i}@example.com", "wrong-pass")).status_code == 401
    assert (await login(client, USER["email"], USER["password"])).status_code == 429


async def test_login_window_expires(client, tokens, monkeypatch):
    import time as time_module

    from app.services import login_limit

    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        await login(client, USER["email"], "wrong-pass")
    assert (await login(client, USER["email"], USER["password"])).status_code == 429
    later = time_module.time() + settings.LOGIN_WINDOW_MINUTES * 60 + 1
    monkeypatch.setattr(login_limit.time, "time", lambda: later)
    assert (await login(client, USER["email"], USER["password"])).status_code == 200


# ── команды администратора ──
async def test_admin_create_user_and_login(client, sessions):
    code, text = await run_admin(sessions, "create-user", "Owner@Shop.ru", "--name", "Владелец")
    assert code == 0 and "Пользователь создан: owner@shop.ru" in text
    password = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Пароль: "))
    assert len(password) >= 12
    assert (await login(client, "owner@shop.ru", password)).status_code == 200
    async with sessions() as db:
        user = await db.scalar(select(User).where(User.email == "owner@shop.ru"))
        assert user.full_name == "Владелец" and user.is_verified
        entry = await db.scalar(select(AuditLog).where(AuditLog.user_id == user.id))
        assert entry.action == "admin.create_user" and entry.user_agent == "admin-cli"
    # повтор — ошибка, без второго аккаунта
    code, text = await run_admin(sessions, "create-user", "owner@shop.ru")
    assert code == 1 and "уже есть" in text
    code, text = await run_admin(sessions, "create-user", "not-an-email")
    assert code == 1 and "Email" in text


async def test_admin_create_user_with_typed_password(client, sessions):
    answers = iter(["typedpass1", "typedpass1"])
    code, text = await run_admin(sessions, "create-user", "me@shop.ru", "--ask", ask=lambda _p: next(answers))
    assert code == 0 and "typedpass1" not in text  # введённый вручную пароль не печатаем
    assert (await login(client, "me@shop.ru", "typedpass1")).status_code == 200
    answers = iter(["short", "short"])
    code, text = await run_admin(sessions, "create-user", "x@shop.ru", "--ask", ask=lambda _p: next(answers))
    assert code == 1 and "минимум 8" in text


async def test_admin_reset_password_ends_sessions_and_unblocks(client, tokens, sessions):
    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        await login(client, USER["email"], "wrong-pass")
    assert (await login(client, USER["email"], USER["password"])).status_code == 429

    code, text = await run_admin(sessions, "reset-password", USER["email"])
    assert code == 0 and "сессии завершены" in text
    password = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Новый пароль: "))
    # старые токены больше не работают, старый пароль тоже
    r = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 401
    assert (await login(client, USER["email"], USER["password"])).status_code == 401
    assert (await login(client, USER["email"], password)).status_code == 200
    async with sessions() as db:
        actions = (await db.scalars(select(AuditLog.action).where(AuditLog.action.like("admin.%")))).all()
        assert actions == ["admin.reset_password"]
        # и в списке «Активные сеансы» прежних входов нет — только вход с новым паролем (шаг 10.26)
        rows = (await db.scalars(select(UserSession))).all()
        assert [x.end_reason for x in rows if x.ended_at is not None] == ["password"]
        assert len([x for x in rows if x.ended_at is None]) == 1

    code, text = await run_admin(sessions, "reset-password", "nobody@example.com")
    assert code == 1 and "не найден" in text


async def test_admin_invite_flow(client, monkeypatch, sessions):
    mode(monkeypatch, "invite")
    monkeypatch.setattr(settings, "SITE_URL", "https://analytics.example.ru")
    code, text = await run_admin(sessions, "invite", "--note", "Иван", "--days", "3")
    assert code == 0 and "действует 3 дн." in text
    invite_code = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Код: "))
    assert f"https://analytics.example.ru/register?invite={invite_code}" in text

    code, text = await run_admin(sessions, "invites")
    assert code == 0 and "действует" in text and "(Иван)" in text and "invite — регистрация только" in text
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": invite_code})
    assert r.status_code == 201
    code, text = await run_admin(sessions, "invites")
    assert "Действующих приглашений нет" in text
    code, text = await run_admin(sessions, "invites", "--all")
    assert "использовано" in text and NEW["email"] in text


async def test_admin_invite_warns_when_mode_not_invite(sessions, monkeypatch):
    mode(monkeypatch, "closed")
    code, text = await run_admin(sessions, "invite")
    assert code == 0 and "REGISTRATION_MODE=invite" in text


async def test_admin_revoke_invite(client, monkeypatch, sessions):
    mode(monkeypatch, "invite")
    async with sessions() as db:
        invite, invite_code = await registration.create_invite(db)
    code, text = await run_admin(sessions, "revoke-invite", str(invite.id)[:8])
    assert code == 0 and "отозвано" in text
    r = await client.post(f"{API}/register", json={**NEW, "invite_code": invite_code})
    assert r.status_code == 400 and "отозвано" in r.json()["detail"]
    code, text = await run_admin(sessions, "revoke-invite", invite_code)
    assert code == 1 and "отзывать нечего" in text
    code, text = await run_admin(sessions, "revoke-invite", "ffffffff")
    assert code == 1 and "не найдено" in text


async def test_admin_invite_for_existing_email_rejected(client, tokens, sessions):
    code, text = await run_admin(sessions, "invite", "--email", USER["email"])
    assert code == 1 and "уже зарегистрирован" in text


async def test_admin_users_deactivate_activate(client, tokens, sessions):
    code, text = await run_admin(sessions, "users")
    assert code == 0 and "Пользователей: 1" in text and USER["email"].lower() in text
    code, text = await run_admin(sessions, "deactivate", USER["email"])
    assert code == 0 and "доступ закрыт" in text
    assert (await login(client, USER["email"], USER["password"])).status_code == 403
    r = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code in (401, 403)
    code, text = await run_admin(sessions, "activate", USER["email"])
    assert code == 0 and "возвращён" in text
    assert (await login(client, USER["email"], USER["password"])).status_code == 200


async def test_admin_unblock(client, tokens, sessions):
    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        await login(client, USER["email"], "wrong-pass")
    assert (await login(client, USER["email"], USER["password"])).status_code == 429
    code, text = await run_admin(sessions, "unblock", USER["email"])
    assert code == 0 and "снята" in text
    assert (await login(client, USER["email"], USER["password"])).status_code == 200


# ── прод: документация API закрыта, настройки проверяются ──
async def test_docs_closed_in_production(monkeypatch, sessions):
    from app.db.session import get_db
    from app.main import create_app

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    prod = create_app()

    async def _get_db():
        async with sessions() as s:
            yield s

    prod.dependency_overrides[get_db] = _get_db
    async with AsyncClient(transport=ASGITransport(app=prod), base_url="http://test") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404, path
        assert (await c.get(f"{API}/registration")).status_code == 200  # сам API работает


async def test_docs_open_in_development(client):
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/openapi.json")).status_code == 200


def test_production_settings_checked(monkeypatch):
    from app.main import check_production_settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", "change_me")
    monkeypatch.setattr(settings, "SITE_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["http://localhost:3000"])
    with pytest.raises(RuntimeError) as e:
        check_production_settings()
    assert "SECRET_KEY" in str(e.value) and "SITE_URL" in str(e.value) and "ALLOWED_ORIGINS" in str(e.value)

    monkeypatch.setattr(settings, "SECRET_KEY", uuid.uuid4().hex + uuid.uuid4().hex)
    monkeypatch.setattr(settings, "SITE_URL", "https://analytics.example.ru")
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["https://analytics.example.ru"])
    check_production_settings()  # всё в порядке — без ошибки

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SECRET_KEY", "change_me")
    check_production_settings()  # при разработке не мешает


# ── /health ──
async def test_health_reports_checks_and_version(client, monkeypatch):
    from app import main

    async def redis_ok():
        return "ok"

    async def redis_down():
        return "error"

    monkeypatch.setattr(settings, "APP_VERSION", "abc1234")
    monkeypatch.setattr(main, "check_redis", redis_ok)
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "abc1234", "db": "ok", "redis": "ok"}

    monkeypatch.setattr(main, "check_redis", redis_down)
    r = await client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "error" and r.json()["redis"] == "error"


# ── Sentry: токен бота не уходит наружу ──
def test_sentry_scrub_removes_bot_token(monkeypatch):
    token = "123456789:AAFakeTokenForTests_abcdefghijklmn"
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", token)
    event = {"message": f"POST https://api.telegram.org/bot{token}/sendMessage failed",
             "breadcrumbs": {"values": [{"data": {"url": f"https://api.telegram.org/bot{token}/getMe"}}]}}
    cleaned = scrub(event)
    assert token not in str(cleaned) and "<скрыто>" in cleaned["message"]
    assert scrub({"message": "без секретов"}) == {"message": "без секретов"}


def test_sentry_off_without_dsn(monkeypatch):
    from app.core.monitoring import init_sentry

    monkeypatch.setattr(settings, "SENTRY_DSN", "")
    assert init_sentry("api") is False
