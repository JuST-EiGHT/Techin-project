"""Аккаунт (шаг 10.26): сеансы входа (список, завершить один или все, смена пароля, токены до 10.26), профиль (имя и
фамилия раздельно, телефон), удаление аккаунта с паролем, отсрочкой и отменой — потом удаляются данные всех магазинов;
новые настройки (минимальная допустимая прибыль, период планирования поставок), «что изменится» в налогах, «Обновить
доступ» для ключа Ozon."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.engine import price_calculator as calc
from app.models.anomaly import Anomaly
from app.models.api_credential import ApiCredential
from app.models.audit import AuditLog
from app.models.notification import NotificationSettings
from app.models.product import Product
from app.models.user import User
from app.models.user_session import UserSession
from app.services import account
from app.services.security import create_access_token, create_refresh_token
from app.services.supply import coverage
from tests.conftest import USER
from tests.test_credentials import API_KEY, ozon, roles_body  # noqa: F401 — фикстура мока Ozon
from tests.test_tax_profile import USN_IP, preview

D = Decimal
URL = "/api/v1/auth"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile Safari/604.1"


def auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


async def login(client, agent: str, ip: str = "10.0.0.1") -> dict:
    r = await client.post(f"{URL}/login", json={"email": USER["email"], "password": USER["password"]},
                          headers={"user-agent": agent, "x-forwarded-for": ip})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- сеансы ----------


def test_device_label():
    assert account.device_label(CHROME) == "Chrome · Windows"
    assert account.device_label(IPHONE) == "Safari · iPhone"
    assert account.device_label("Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/131.0") == "Firefox · Linux"
    assert account.device_label("Mozilla/5.0 (Macintosh) Chrome/130.0 YaBrowser/24.10 Safari/537.36") == \
        "Яндекс Браузер · macOS"
    assert account.device_label(None) == "Неизвестное устройство"


async def test_sessions_list_end_one_and_others(client, tokens):
    pc = await login(client, CHROME, "10.0.0.1")
    phone = await login(client, IPHONE, "10.0.0.2")
    sessions = (await client.get(f"{URL}/sessions", headers=auth(pc))).json()
    assert len(sessions) == 3  # фикстура tokens, компьютер, телефон
    assert sessions[0]["current"] is True and sessions[0]["device"] == "Chrome · Windows"
    assert {s["device"] for s in sessions[1:]} >= {"Safari · iPhone"}
    phone_id = next(s["id"] for s in sessions if s["device"] == "Safari · iPhone")
    assert next(s for s in sessions if s["id"] == phone_id)["ip"] == "10.0.0.2"

    # завершить телефон: его токен доступа и обновления больше не принимаются
    assert (await client.delete(f"{URL}/sessions/{phone_id}", headers=auth(pc))).status_code == 204
    r = await client.get(f"{URL}/me", headers=auth(phone))
    assert (r.status_code, r.json()["detail"]) == (401, "Сеанс завершён — войдите снова")
    r = await client.post(f"{URL}/refresh", json={"refresh_token": phone["refresh_token"]})
    assert (r.status_code, r.json()["detail"]) == (401, "Сеанс завершён — войдите снова")
    assert (await client.get(f"{URL}/me", headers=auth(pc))).status_code == 200  # этот — работает

    # обновление токена — тот же сеанс: последняя активность и адрес
    r = await client.post(f"{URL}/refresh", json={"refresh_token": pc["refresh_token"]},
                          headers={"user-agent": CHROME, "x-forwarded-for": "10.0.0.9"})
    pc = r.json()
    sessions = (await client.get(f"{URL}/sessions", headers=auth(pc))).json()
    assert len(sessions) == 2 and sessions[0]["current"] and sessions[0]["ip"] == "10.0.0.9"

    # «завершить все, кроме этого» — сеанс фикстуры закрыт
    assert (await client.post(f"{URL}/sessions/end-others", headers=auth(pc))).json() == {"ended": 1}
    assert (await client.get(f"{URL}/me", headers=auth(tokens))).status_code == 401
    assert [s["current"] for s in (await client.get(f"{URL}/sessions", headers=auth(pc))).json()] == [True]
    # чужой сеанс завершить нельзя
    assert (await client.delete(f"{URL}/sessions/{uuid.uuid4()}", headers=auth(pc))).status_code == 404

    # выход — сеанс завершён (причина — logout)
    await client.post(f"{URL}/logout", json={"refresh_token": pc["refresh_token"]}, headers=auth(pc))
    again = await login(client, CHROME)
    assert len((await client.get(f"{URL}/sessions", headers=auth(again))).json()) == 1


async def test_password_change_ends_all_sessions_and_legacy_tokens(client, tokens, sessions):
    uid = uuid.UUID((await client.get(f"{URL}/me", headers=auth(tokens))).json()["id"])
    # токен до 10.26 — без сеанса: работает, а при обновлении получает сеанс
    legacy = {"access_token": create_access_token(uid), "refresh_token": create_refresh_token(uid)}
    assert (await client.get(f"{URL}/me", headers=auth(legacy))).status_code == 200
    refreshed = (await client.post(f"{URL}/refresh", json={"refresh_token": legacy["refresh_token"]})).json()
    assert len((await client.get(f"{URL}/sessions", headers=auth(refreshed))).json()) == 2

    r = await client.post(f"{URL}/change-password", json={"current_password": USER["password"],
                                                         "new_password": "newpassword123"}, headers=auth(tokens))
    assert r.status_code == 204
    async with sessions() as db:
        rows = list((await db.execute(select(UserSession).where(UserSession.user_id == uid))).scalars())
    assert rows and all(s.ended_at is not None and s.end_reason in ("password",) for s in rows)


# ---------- профиль ----------


async def test_profile_first_last_name_and_phone(client, tokens):
    me = (await client.get(f"{URL}/me", headers=auth(tokens))).json()
    assert (me["first_name"], me["last_name"], me["phone"]) == ("Радимир", None, None)  # из одного поля при регистрации
    patch = lambda body: client.patch(f"{URL}/me", json=body, headers=auth(tokens))  # noqa: E731
    r = await patch({"first_name": " Анна ", "last_name": "Петрова", "phone": "8 (912) 345-67-89"})
    assert (r.json()["first_name"], r.json()["last_name"], r.json()["full_name"], r.json()["phone"]) == (
        "Анна", "Петрова", "Анна Петрова", "+79123456789")
    r = await patch({"phone": "+44 20 7946 0958"})
    assert r.json()["phone"] == "+442079460958" and r.json()["first_name"] == "Анна"  # не переданное не меняется
    r = await patch({"phone": "позвоните мне"})
    assert (r.status_code, r.json()["detail"]) == (400, "Телефон: цифрами, например +7 912 345-67-89")
    r = await patch({"phone": ""})
    assert r.json()["phone"] is None  # необязательный
    r = await patch({"full_name": "Мария Сергеевна Иванова"})  # прежнее одно поле
    assert (r.json()["first_name"], r.json()["last_name"]) == ("Мария", "Сергеевна Иванова")


# ---------- удаление аккаунта ----------


async def test_deletion_with_password_delay_and_cancel(client, tokens, sessions):
    r = await client.post(f"{URL}/account/delete", json={"password": "wrong"}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Пароль указан неверно")
    r = await client.post(f"{URL}/account/delete", json={"password": USER["password"]}, headers=auth(tokens))
    due = datetime.fromisoformat(r.json()["deletion_due_at"].replace("Z", "+00:00"))
    due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
    assert timedelta(days=29, hours=23) < due - datetime.now(timezone.utc) <= timedelta(days=30)
    again = await client.post(f"{URL}/account/delete", json={"password": USER["password"]}, headers=auth(tokens))
    assert again.json()["deletion_due_at"] == r.json()["deletion_due_at"]  # повтор не сдвигает срок
    # до срока аккаунт работает, удалять нечего
    async with sessions() as db:
        assert await account.purge_due(db) == []
    r = await client.post(f"{URL}/account/cancel-deletion", headers=auth(tokens))
    assert r.json()["deletion_due_at"] is None
    async with sessions() as db:
        assert await account.purge_due(db, now=datetime.now(timezone.utc) + timedelta(days=31)) == []


async def test_purge_removes_account_and_all_stores(client, tokens, sessions):
    uid = uuid.UUID((await client.get(f"{URL}/me", headers=auth(tokens))).json()["id"])
    other = {"email": "keep@example.com", "password": "keeppass123", "full_name": "Другой Продавец"}
    await client.post(f"{URL}/register", json=other)
    async with sessions() as db:
        keep = (await db.execute(select(User).where(User.email == other["email"]))).scalar_one()
        store = User(id=uuid.uuid4(), owner_id=uid, store_name="Второй магазин")
        db.add(store)
        await db.flush()
        now = datetime.now(timezone.utc)
        for owner in (uid, store.id, keep.id):
            db.add(Product(user_id=owner, offer_id=f"A-{owner}", name="Товар", ozon_product_id=1, synced_at=now))
            db.add(Anomaly(id=uuid.uuid4(), user_id=owner, type="sales_drop", key="shop", severity="red",
                           detected_at=now, last_seen_at=now))
            db.add(AuditLog(user_id=owner, created_at=now, action="test", entity_type="http", entity_id="x", status="ok"))
        db.add(NotificationSettings(user_id=uid))
        db.add(ApiCredential(user_id=store.id, label="Ключ", client_id="1", api_key_encrypted="x", is_active=True,
                             expires_at=now + timedelta(days=30)))
        await db.commit()
    r = await client.post(f"{URL}/account/delete", json={"password": USER["password"]}, headers=auth(tokens))
    assert r.status_code == 200
    async with sessions() as db:
        assert await account.purge_due(db, now=datetime.now(timezone.utc) + timedelta(days=30, minutes=1)) == [uid]
        gone = [uid, store.id]
        for model in (User, Product, Anomaly, AuditLog, NotificationSettings, ApiCredential, UserSession):
            col = model.id if model is User else model.user_id
            assert await db.scalar(select(func.count()).select_from(model).where(col.in_(gone))) == 0, model
        # чужие данные не тронуты
        assert await db.scalar(select(func.count()).select_from(Product).where(Product.user_id == keep.id)) == 1
        assert await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.user_id == keep.id)) == 1
    assert (await client.get(f"{URL}/me", headers=auth(tokens))).status_code == 401


# ---------- настройки, налоги, ключ Ozon ----------


def test_price_with_minimum_profit():
    p = calc.PriceParams(cost=D(500), supplier_vat=D(1), commission_pct=D(20), logistics=D(100))
    # π(P) = 0,8 × P − 600: без убытка — 750, с прибылью 200 ₽ — 1 000
    r = calc.calculate(p, D(900), min_profit=D(200))
    assert (r.min_price, r.floor_price, r.floor_ok) == (D("750.00"), D("1000.00"), False)
    assert calc.calculate(p, D(1000), min_profit=D(200)).floor_ok is True
    assert calc.calculate(p, D(900)).floor_price is None  # не задана — нет


def test_supply_demand_window():
    state = type("S", (), {"synced_at": datetime(2026, 10, 26, 9, tzinfo=timezone.utc), "loaded_from": date(2026, 7, 1),
                           "scheme": "fbo"})()
    cov = coverage(state, date(2026, 10, 26))
    assert (cov.window_from, cov.window_to, cov.days_30) == (date(2026, 9, 26), date(2026, 10, 25), 30)
    cov = coverage(state, date(2026, 10, 26), 60)
    assert (cov.window_from, cov.days_30) == (date(2026, 8, 27), 60)


async def test_new_settings(client, tokens):
    s = (await client.get("/api/v1/settings", headers=auth(tokens))).json()
    assert (s["supply_demand_days"], s["min_profit_rub"]) == (30, "0")
    r = await client.patch("/api/v1/settings", json={"supply_demand_days": 60, "min_profit_rub": "150.50"},
                           headers=auth(tokens))
    assert (r.json()["supply_demand_days"], D(r.json()["min_profit_rub"])) == (60, D("150.5"))
    assert (await client.patch("/api/v1/settings", json={"supply_demand_days": 3}, headers=auth(tokens))).status_code == 400
    assert (await client.patch("/api/v1/settings", json={"min_profit_rub": "-1"}, headers=auth(tokens))).status_code == 400


async def test_tax_preview_impact(client, tokens):
    p = await preview(client, tokens, USN_IP)  # первый профиль — на всю историю
    assert p["impact"][0].startswith("P&L, налог и чистая прибыль за любой период посчитаются по этому профилю")
    p = await preview(client, tokens, {**USN_IP, "effective_from": "2026-01-01"})
    assert p["impact"][0].startswith("P&L, налог и чистая прибыль за любой период с 01.01.2026")
    assert any(line.startswith("Минимальная и целевая цена") for line in p["impact"])
    p = await preview(client, tokens, {**USN_IP, "effective_from": "2099-01-01"})
    assert any(line.startswith("Цены и маржа на страницах — по прежним условиям до 01.01.2099") for line in p["impact"])


async def test_replace_ozon_key(client, tokens, ozon):  # noqa: F811
    r = await client.post("/api/v1/credentials", json={"label": "Ключ", "client_id": "1234567", "api_key": API_KEY},
                          headers=auth(tokens))
    cred_id = r.json()["id"]
    r = await client.post(f"/api/v1/credentials/{cred_id}/replace", json={"api_key": "new-key-0000"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["credential"]["is_active"] is True
    ozon.routes["roles"].respond(403, json={"code": 7, "message": "Invalid Api-Key"})
    ozon.routes["product_list"].respond(403, json={"code": 7, "message": "Invalid Api-Key"})
    r = await client.post(f"/api/v1/credentials/{cred_id}/replace", json={"api_key": "bad"}, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"].startswith("Ключ не обновлён")
