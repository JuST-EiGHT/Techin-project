"""Несколько магазинов у одного аккаунта (шаг 8.4b): данные магазинов не пересекаются, переключение — заголовком
X-Store-Id, чужой магазин неотличим от несуществующего, один магазин — один кабинет Ozon, тема — общая на аккаунт,
журнал — по магазину, уведомления — в Telegram владельца с названием магазина."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import respx
from sqlalchemy import select, update

from app.core.config import settings
from app.models.api_credential import ApiCredential
from app.models.audit import AuditLog
from app.models.product import Product
from app.models.sync_run import SyncRun
from app.models.user import User
from app.services.security import create_access_token
from app.services.sync.runner import run_sync
from app.tasks.sync import users_with_active_keys
from tests.test_credentials import roles_body
from tests.test_deploy import run_admin
from tests.test_notifications import CHAT, SITE, link, tg  # noqa: F401 — фикстура tg
from tests.test_products import seed, user_id_of

STORES = "/api/v1/stores"
KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"
MAIN_CLIENT, SECOND_CLIENT = "1234567", "7654321"


def auth(tokens, store=None):
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    if store is not None:
        h["X-Store-Id"] = str(store)
    return h


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        mock.post("/v1/roles").respond(200, json=roles_body())
        mock.post("/v3/product/list").respond(200, json={"result": {"items": [], "total_items": 0, "last_id": ""}})
        yield mock


async def new_store(client, tokens, client_id=SECOND_CLIENT, **over) -> dict:
    r = await client.post(STORES, json={"client_id": client_id, "api_key": KEY, **over}, headers=auth(tokens))
    assert r.status_code == 201, r.text
    return r.json()


async def offers(client, tokens, store=None) -> list[str]:
    r = await client.get("/api/v1/products", headers=auth(tokens, store))
    assert r.status_code == 200, r.text
    return [p["offer_id"] for p in r.json()["items"]]


async def test_first_store_is_the_account(client, tokens):
    r = await client.get(STORES, headers=auth(tokens))
    assert r.status_code == 200
    [main] = r.json()
    assert main["id"] == str(await user_id_of(client, tokens))
    assert (main["name"], main["is_main"], main["is_active"], main["client_id_masked"], main["keys"]) == (
        "Основной магазин", True, True, None, 0)


async def test_new_store_has_own_data(client, tokens, sessions, ozon, no_celery):
    """Новый магазин — со своим ключом и сразу загрузкой из Ozon; товары, ключи, настройки магазинов не пересекаются."""
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    store = await new_store(client, tokens, name="  Второй   магазин ")
    sid = uuid.UUID(store["id"])
    assert (store["name"], store["is_main"], store["keys"], store["active_keys"]) == ("Второй магазин", False, 1, 1)
    assert store["client_id_masked"].endswith("4321") and SECOND_CLIENT not in store["client_id_masked"]
    assert sorted(kind for kind, user_id, *_ in no_celery if user_id == sid) == sorted(
        ["products", "stocks", "promotions", "finance", "balance", "postings", "supplies"])

    assert await offers(client, tokens) == ["6000-ZZ", "6001-2RS.C3", "ACB305523"]
    assert await offers(client, tokens, sid) == []  # каталог второго магазина пуст — товары первого не видны
    async with sessions() as db:
        db.add(Product(user_id=sid, ozon_product_id=1, offer_id="B-ONLY", synced_at=datetime.now(timezone.utc)))
        await db.commit()
    assert await offers(client, tokens, sid) == ["B-ONLY"]
    assert "B-ONLY" not in await offers(client, tokens)

    keys = (await client.get("/api/v1/credentials", headers=auth(tokens, sid))).json()
    assert [k["client_id_masked"][-4:] for k in keys] == ["4321"]
    assert (await client.get("/api/v1/credentials", headers=auth(tokens))).json() == []

    # настройки магазина — свои; тема — одна на аккаунт
    r = await client.patch("/api/v1/settings", json={"supplier_payment": "after_sale", "theme": "light"},
                           headers=auth(tokens, sid))
    assert r.status_code == 200 and (r.json()["supplier_payment"], r.json()["theme"]) == ("after_sale", "light")
    main = (await client.get("/api/v1/settings", headers=auth(tokens))).json()
    assert (main["supplier_payment"], main["theme"]) == ("upfront", "light")

    # фоновые задачи — по магазину: у каждого свой ключ
    async with sessions() as db:
        assert await users_with_active_keys(db) == [sid]


async def test_foreign_or_unknown_store_is_404(client, tokens, sessions, ozon):
    store = await new_store(client, tokens)
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    other_tokens = (await client.post("/api/v1/auth/login", json=other)).json()
    for header in (store["id"], str(uuid.uuid4()), "not-a-uuid"):
        r = await client.get("/api/v1/products", headers=auth(other_tokens, header))
        assert r.status_code == 404 and "Магазин не найден" in r.json()["detail"]
    assert (await client.get(STORES, headers=auth(other_tokens))).json()[0]["name"] == "Основной магазин"
    assert len((await client.get(STORES, headers=auth(other_tokens))).json()) == 1
    # свой первый магазин можно указать явно (в любом регистре)
    main_id = str(await user_id_of(client, tokens))
    for header in (main_id, main_id.upper()):
        assert (await client.get("/api/v1/products", headers=auth(tokens, header))).status_code == 200


async def test_one_store_one_ozon_cabinet(client, tokens, sessions, ozon):
    """Ключ другого кабинета в магазин не добавить — только отдельным магазином; кабинет не подключить дважды."""
    body = {"label": "Ключ", "client_id": MAIN_CLIENT, "api_key": KEY}
    assert (await client.post("/api/v1/credentials", json=body, headers=auth(tokens))).status_code == 201
    r = await client.post("/api/v1/credentials", json=body, headers=auth(tokens))  # новый ключ того же кабинета
    assert r.status_code == 201
    r = await client.post("/api/v1/credentials", json={**body, "client_id": SECOND_CLIENT}, headers=auth(tokens))
    assert r.status_code == 409 and "отдельным магазином" in r.json()["detail"]

    r = await client.post(STORES, json={"client_id": MAIN_CLIENT, "api_key": KEY}, headers=auth(tokens))
    assert r.status_code == 409 and "уже подключён в магазине «Основной магазин»" in r.json()["detail"]
    store = await new_store(client, tokens, label="Склад в Казани")
    assert store["name"] == "Склад в Казани"  # без названия — по названию ключа
    r = await client.post("/api/v1/credentials", json=body, headers=auth(tokens, store["id"]))
    assert r.status_code == 400 and "уже подключён в магазине «Основной магазин»" in r.json()["detail"]
    async with sessions() as db:
        assert {c.client_id for c in (await db.scalars(select(ApiCredential))).all()} == {MAIN_CLIENT, SECOND_CLIENT}


async def test_empty_store_can_change_cabinet(client, tokens, sessions, ozon):
    """Ошиблись кабинетом: пока у магазина нет ключей и товаров, ключ другого кабинета принимается."""
    body = {"client_id": MAIN_CLIENT, "api_key": KEY}
    cred = (await client.post("/api/v1/credentials", json=body, headers=auth(tokens))).json()
    await client.delete(f"/api/v1/credentials/{cred['id']}", headers=auth(tokens))
    r = await client.post("/api/v1/credentials", json={**body, "client_id": SECOND_CLIENT}, headers=auth(tokens))
    assert r.status_code == 201
    [main] = (await client.get(STORES, headers=auth(tokens))).json()
    assert main["client_id_masked"].endswith("4321")


async def test_rename_disable_enable(client, tokens, sessions, ozon):
    store = await new_store(client, tokens)
    sid = store["id"]
    r = await client.patch(f"{STORES}/{sid}", json={"name": "Основной магазин"}, headers=auth(tokens))
    assert r.status_code == 409 and "уже есть" in r.json()["detail"]
    r = await client.patch(f"{STORES}/{sid}", json={"name": "Подшипники Юг"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["name"] == "Подшипники Юг"
    main_id = str(await user_id_of(client, tokens))
    r = await client.patch(f"{STORES}/{main_id}", json={"is_active": False}, headers=auth(tokens))
    assert r.status_code == 400 and "его не отключить" in r.json()["detail"]

    r = await client.patch(f"{STORES}/{sid}", json={"is_active": False}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.get("/api/v1/products", headers=auth(tokens, sid))).status_code == 404
    async with sessions() as db:
        assert await users_with_active_keys(db) == []  # отключённый не синхронизируется, ключ и данные сохранены
        assert await db.scalar(select(ApiCredential.id).where(ApiCredential.user_id == uuid.UUID(sid))) is not None
    r = await client.patch(f"{STORES}/{sid}", json={"is_active": True}, headers=auth(tokens))
    assert r.json()["is_active"] is True
    assert (await client.get("/api/v1/products", headers=auth(tokens, sid))).status_code == 200

    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    other_tokens = (await client.post("/api/v1/auth/login", json=other)).json()
    r = await client.patch(f"{STORES}/{sid}", json={"name": "Чужой"}, headers=auth(other_tokens))
    assert r.status_code == 404


async def test_closed_account_stops_all_its_stores(client, tokens, sessions, ozon):
    store = await new_store(client, tokens)
    code, _ = await run_admin(sessions, "deactivate", "seller@example.com")
    assert code == 0
    async with sessions() as db:
        assert await users_with_active_keys(db) == []
        assert (await db.get(User, uuid.UUID(store["id"]))).is_active  # сам магазин не трогаем — вернётся с доступом


async def test_store_is_not_an_account(client, tokens, sessions, ozon):
    store = await new_store(client, tokens)
    forged = create_access_token(uuid.UUID(store["id"]))
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
    code, out = await run_admin(sessions, "users")
    assert code == 0 and "Пользователей: 1" in out and "магазинов: 2" in out


async def test_audit_log_belongs_to_store(client, tokens, sessions, ozon):
    store = await new_store(client, tokens)
    await client.patch("/api/v1/settings", json={"stock_low_days": 20}, headers=auth(tokens, store["id"]))
    async with sessions() as db:
        entry = (await db.scalars(select(AuditLog).where(AuditLog.path == "/api/v1/settings"))).one()
    assert entry.user_id == uuid.UUID(store["id"])


async def test_alerts_of_store_go_to_owner_telegram(client, tokens, sessions, ozon, tg):  # noqa: F811
    """Аномалии второго магазина — в чат аккаунта, с названием магазина и ссылкой, открывающей сайт в нём."""
    uid = await user_id_of(client, tokens)
    store = await new_store(client, tokens, name="Юг")
    sid = uuid.UUID(store["id"])
    await seed(sessions, sid)
    await link(sessions, uid)
    run = await run_sync("anomalies", sid, sessions)
    assert run.status == "success" and run.stats["telegram"]["sent"] == 5
    [msg] = tg.sent
    assert int(msg["chat_id"]) == CHAT
    assert "Юг: <a href=" in msg["text"] and f"store={sid}" in msg["text"]
    stores = (await client.get(STORES, headers=auth(tokens))).json()
    assert [s["unread_alerts"] for s in stores] == [0, 5]
    r = await client.get("/api/v1/notifications/settings", headers=auth(tokens))
    assert r.json()["last_sent_at"] is not None  # журнал отправок — по всем магазинам аккаунта


async def test_admin_resync_queues_every_store(client, tokens, sessions, ozon, no_celery):
    await client.post("/api/v1/credentials", json={"client_id": MAIN_CLIENT, "api_key": KEY}, headers=auth(tokens))
    store = await new_store(client, tokens)
    no_celery.clear()
    async with sessions() as db:  # загрузки при подключении ключей закончились
        await db.execute(update(SyncRun).values(status="success"))
        await db.commit()
    code, out = await run_admin(sessions, "resync", "seller@example.com")
    assert code == 0 and "«Основной магазин»: в очереди" in out
    queued = {(kind, str(user_id)) for kind, user_id, *_ in no_celery}
    for sid in (str(await user_id_of(client, tokens)), store["id"]):
        assert {k for k, s in queued if s == sid} == {"products", "stocks", "promotions", "postings", "supplies", "finance", "balance"}
    finance = next(args for args in no_celery if args[0] == "finance")
    assert finance[3]["date_to"] == (datetime.fromisoformat(finance[3]["date_from"]) + timedelta(days=89)).date().isoformat()
