"""Основные настройки, профиль и смена пароля (шаг 0.5)."""
from pathlib import Path

from tests.conftest import USER

FIXTURE = Path(__file__).parent / "fixtures" / "accrual_2026-09-07_13.xlsx"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_settings_defaults_and_update(client, tokens):
    get = lambda: client.get("/api/v1/settings", headers=auth(tokens))  # noqa: E731
    assert (await client.get("/api/v1/settings")).status_code == 401
    assert (await get()).json() == {"theme": "dark"}  # налог и НДС — в налоговом профиле (шаг 2.5)

    r = await client.patch("/api/v1/settings", json={"theme": "light"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json() == {"theme": "light"}
    assert (await get()).json()["theme"] == "light"  # сохраняется — одинаково на всех устройствах
    r = await client.patch("/api/v1/settings", json={}, headers=auth(tokens))
    assert r.json()["theme"] == "light"  # не переданное не меняется

    r = await client.patch("/api/v1/settings", json={"theme": "blue"}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Тема: недопустимое значение")


async def test_new_reports_follow_tax_profile_until_edited(client, tokens):
    r = await client.post(
        "/api/v1/reports/upload", headers=auth(tokens), files={"file": ("a.xlsx", FIXTURE.read_bytes(), XLSX_TYPE)}
    )
    body = r.json()
    assert (body["vat_multiplier"], body["tax_base"], body["tax_rate"]) == (None, None, None)
    # в отчёте можно поправить, null — снова по налоговому профилю
    url = f"/api/v1/reports/{body['id']}"
    body = (await client.patch(url, json={"tax_base": "profit", "tax_rate": "0.15"}, headers=auth(tokens))).json()
    assert (body["tax_base"], body["tax_rate"]) == ("profit", "0.1500")
    body = (await client.patch(url, json={"tax_base": None, "tax_rate": None}, headers=auth(tokens))).json()
    assert (body["tax_base"], body["tax_rate"]) == (None, None)


async def test_update_profile_name(client, tokens):
    r = await client.patch("/api/v1/auth/me", json={"full_name": "  Иван Петров "}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["full_name"] == "Иван Петров"
    r = await client.patch("/api/v1/auth/me", json={}, headers=auth(tokens))
    assert r.json()["full_name"] == "Иван Петров"  # не переданное поле не меняется
    r = await client.patch("/api/v1/auth/me", json={"full_name": ""}, headers=auth(tokens))
    assert r.json()["full_name"] is None
    r = await client.patch("/api/v1/auth/me", json={"email": "x@example.com"}, headers=auth(tokens))
    assert r.json()["email"] == USER["email"].lower()  # email так не меняется


def test_tokens_issued_in_same_second_before_change_are_revoked():
    from datetime import datetime, timezone

    from app.models.user import User
    from app.services.auth import issued_before_revocation

    changed = datetime(2026, 10, 13, 12, 0, 0, 500_000, tzinfo=timezone.utc)  # 12:00:00.500
    user = User(tokens_valid_after=changed)
    second = int(changed.timestamp())
    assert issued_before_revocation(user, {"iat": second, "iat_ms": second * 1000 + 499})  # 12:00:00.499
    assert not issued_before_revocation(user, {"iat": second, "iat_ms": second * 1000 + 501})  # после смены
    assert issued_before_revocation(User(tokens_valid_after=changed.replace(tzinfo=None)), {"iat": second - 1})
    assert not issued_before_revocation(User(tokens_valid_after=None), {"iat": 0})


async def test_change_password_logs_out_everywhere(client, tokens):
    creds = {"email": USER["email"], "password": USER["password"]}
    second = (await client.post("/api/v1/auth/login", json=creds)).json()
    change = lambda body, t=tokens: client.post("/api/v1/auth/change-password", json=body, headers=auth(t))  # noqa: E731

    r = await change({"current_password": "wrong-pass", "new_password": "newstrongpass1"})
    assert (r.status_code, r.json()["detail"]) == (400, "Текущий пароль указан неверно")
    r = await change({"current_password": USER["password"], "new_password": "short"})
    assert (r.status_code, r.json()["detail"]) == (400, "Новый пароль: минимум 8 символов")
    r = await change({"current_password": USER["password"], "new_password": USER["password"]})
    assert (r.status_code, r.json()["detail"]) == (400, "Новый пароль совпадает с текущим")

    assert (await change({"current_password": USER["password"], "new_password": "newstrongpass1"})).status_code == 204
    # все сессии завершены: и текущая, и на другом устройстве, и их refresh-токены
    for t in (tokens, second):
        r = await client.get("/api/v1/auth/me", headers=auth(t))
        assert r.status_code == 401
    r = await client.get("/api/v1/auth/me", headers=auth(second))
    assert r.json()["detail"] == "Пароль изменён — войдите снова с новым паролем"
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert (r.status_code, r.json()["detail"]) == (401, "Пароль изменён — войдите снова с новым паролем")

    # старый пароль не подходит, новый — да, и новая сессия работает
    old = await client.post("/api/v1/auth/login", json={"email": USER["email"], "password": USER["password"]})
    assert old.status_code == 401
    new = await client.post("/api/v1/auth/login", json={"email": USER["email"], "password": "newstrongpass1"})
    assert new.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=auth(new.json()))).status_code == 200
