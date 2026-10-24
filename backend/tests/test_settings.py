"""Основные настройки, профиль и смена пароля (шаг 0.5)."""
from pathlib import Path

from tests.conftest import USER

FIXTURE = Path(__file__).parent / "fixtures" / "accrual_2026-09-07_13.xlsx"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# пороги аномалий и «низкой маржи» по умолчанию (шаг 7.1, BUSINESS_LOGIC §7)
ANOMALY_DEFAULTS = {
    "low_margin_pct": "10", "anomaly_sales_drop_pct": 40, "anomaly_sales_drop_red_pct": 70,
    "anomaly_sales_growth_pct": 200, "anomaly_price_pct": 10, "anomaly_price_red_pct": 30, "anomaly_commission_pp": "2",
    "anomaly_payout_diff_pct": 10, "anomaly_cash_gap_days": 14, "anomaly_uncategorized_days": 7,
    "anomaly_sync_stale_hours": 24, "anomaly_data_gap_days": 1, "anomaly_defaults": True,
    "recommendation_hide_days": 7,
    "abc_a_pct": 80,
    "abc_b_pct": 95,
}


async def test_settings_defaults_and_update(client, tokens):
    get = lambda: client.get("/api/v1/settings", headers=auth(tokens))  # noqa: E731
    assert (await client.get("/api/v1/settings")).status_code == 401
    # налог и НДС — в налоговом профиле (шаг 2.5)
    # калькулятор цены (шаг 4.2): реклама 0 % и маржа 20 % — по умолчанию, не цифры владельца
    assert (await get()).json() == {"theme": "dark", "supplier_payment": "upfront", "supplier_deferral_days": None,
                                    "cost_label": "Себестоимость с НДС", "price_ad_pct": "0",
                                    "price_target_margin_pct": "20", "price_defaults": True,
                                    "stock_critical_days": 7, "stock_low_days": 14, "stock_excess_days": 60,
                                    "supply_target_days": 45, "supply_lead_time_days": 7, "supply_safety_days": 0, **ANOMALY_DEFAULTS}

    r = await client.patch("/api/v1/settings", json={"theme": "light"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["theme"] == "light"
    assert (await get()).json()["theme"] == "light"  # сохраняется — одинаково на всех устройствах
    r = await client.patch("/api/v1/settings", json={}, headers=auth(tokens))
    assert r.json()["theme"] == "light"  # не переданное не меняется

    r = await client.patch("/api/v1/settings", json={"theme": "blue"}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Тема: недопустимое значение")


async def test_supplier_payment_names_cost_in_pnl(client, tokens):
    """Поставщик получает деньги после продажи — себестоимость с НДС в P&L называется «долг поставщику» (шаг 3.3)."""
    r = await client.patch("/api/v1/settings", json={"supplier_payment": "after_sale"}, headers=auth(tokens))
    assert r.json() == {"theme": "dark", "supplier_payment": "after_sale", "supplier_deferral_days": None,
                        "cost_label": "Долг поставщику", "price_ad_pct": "0", "price_target_margin_pct": "20",
                        "price_defaults": True, "stock_critical_days": 7, "stock_low_days": 14, "stock_excess_days": 60,
                        "supply_target_days": 45, "supply_lead_time_days": 7, "supply_safety_days": 0, **ANOMALY_DEFAULTS}
    r = await client.patch("/api/v1/settings", json={"theme": "light"}, headers=auth(tokens))
    assert r.json()["supplier_payment"] == "after_sale"  # не переданное не меняется
    r = await client.patch("/api/v1/settings", json={"supplier_payment": "later"}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Откуда товар: недопустимое значение")


async def test_where_goods_come_from(client, tokens):
    """«Откуда товар» (шаг 3.8): своё производство — «Себестоимость производства», отсрочка — с числом дней."""
    patch = lambda body: client.patch("/api/v1/settings", json=body, headers=auth(tokens))  # noqa: E731
    r = await patch({"supplier_payment": "own_production"})
    assert r.json()["cost_label"] == "Себестоимость производства"
    r = await patch({"supplier_payment": "deferred"})
    assert (r.status_code, r.json()["detail"]) == (
        400, "Укажите отсрочку оплаты поставщику — через сколько дней после закупки платите"
    )
    r = await patch({"supplier_payment": "deferred", "supplier_deferral_days": 30})
    assert (r.json()["supplier_deferral_days"], r.json()["cost_label"]) == (30, "Себестоимость с НДС")
    r = await patch({"supplier_deferral_days": 45})
    assert r.json()["supplier_deferral_days"] == 45
    r = await patch({"supplier_payment": "mixed"})
    assert (r.json()["supplier_payment"], r.json()["supplier_deferral_days"]) == ("mixed", None)  # отсрочка сброшена
    r = await patch({"supplier_deferral_days": 400})
    assert r.status_code == 400


async def test_stock_thresholds(client, tokens):
    """Пороги статуса остатка (шаг 5.1): по умолчанию 7 / 14 / 60 дней, меняются по одному, идут по возрастанию."""
    patch = lambda body: client.patch("/api/v1/settings", json=body, headers=auth(tokens))  # noqa: E731
    days = lambda r: (r.json()["stock_critical_days"], r.json()["stock_low_days"], r.json()["stock_excess_days"])  # noqa: E731
    r = await patch({"stock_low_days": 21})
    assert r.status_code == 200 and days(r) == (7, 21, 60)
    r = await patch({"stock_critical_days": 10, "stock_excess_days": 90})
    assert days(r) == (10, 21, 90)
    r = await patch({"stock_critical_days": 21})
    assert (r.status_code, r.json()["detail"]) == (
        400, "Пороги остатков идут по возрастанию: «Критично» меньше «Мало», «Мало» меньше «Избыток»"
    )
    r = await patch({"stock_excess_days": 20})
    assert r.status_code == 400
    r = await patch({"stock_excess_days": 400})
    assert r.status_code == 400
    assert days(await client.get("/api/v1/settings", headers=auth(tokens))) == (10, 21, 90)  # ошибка ничего не меняет


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


async def test_anomaly_thresholds(client, tokens):
    """Пороги аномалий и «низкой маржи» (шаг 7.1): меняются по одному, жёлтый порог меньше красного, вернуть — умолчания."""
    patch = lambda body: client.patch("/api/v1/settings", json=body, headers=auth(tokens))  # noqa: E731
    r = await patch({"low_margin_pct": "12.5", "anomaly_sales_drop_pct": 30, "anomaly_commission_pp": "1.5"})
    assert r.status_code == 200
    body = r.json()
    assert (body["low_margin_pct"], body["anomaly_sales_drop_pct"], body["anomaly_sales_drop_red_pct"],
            body["anomaly_commission_pp"], body["anomaly_defaults"]) == ("12.5", 30, 70, "1.5", False)
    r = await patch({"anomaly_sales_drop_red_pct": 25})
    assert (r.status_code, r.json()["detail"]) == (400, "Падение продаж: жёлтый порог меньше красного, оба — от 1 до 99 %")
    r = await patch({"anomaly_price_pct": 30})
    assert (r.status_code, r.json()["detail"]) == (400, "Изменение цены: жёлтый порог меньше красного")
    r = await patch({"anomaly_cash_gap_days": 0})
    assert r.status_code == 400 and r.json()["detail"].startswith("Кассовый разрыв — дней вперёд")
    r = await patch({"low_margin_pct": "10", "anomaly_sales_drop_pct": 40, "anomaly_commission_pp": "2"})
    assert r.json()["anomaly_defaults"] is True  # вернули умолчания
