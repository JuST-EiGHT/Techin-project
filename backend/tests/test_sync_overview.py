"""Шаг 10.2: кнопка «Синхронизация» — «Обновить всё», статус по видам данных, ошибки простыми словами и без секретов."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import settings
from app.integrations.ozon.errors import OzonAuthError, OzonRateLimitError, OzonServerError
from app.models.api_credential import ApiCredential
from app.models.bank import BankConnection
from app.models.sync_run import FAILED, QUEUED, RUNNING, SUCCESS, SyncRun
from app.services.encryption import encrypt
from app.services.sync import overview, runner

API = "/api/v1/sync"
API_KEY = "secret-ozon-key-0123456789"


def now() -> datetime:  # не константа модуля: полный прогон тестов идёт минуты, «5 минут назад» должно быть от сейчас
    return datetime.now(timezone.utc)


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def me(client, tokens) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])


async def add_key(sessions, user_id, expires=timedelta(days=100)):
    async with sessions() as db:
        db.add(ApiCredential(user_id=user_id, label="k", client_id="1234567", api_key_encrypted=encrypt(API_KEY),
                             is_active=True, expires_at=now() + expires))
        await db.commit()


async def add_run(sessions, user_id, kind, status, ago=timedelta(minutes=1), error=None, stats=None):
    async with sessions() as db:
        at = now() - ago
        db.add(SyncRun(user_id=user_id, kind=kind, status=status, started_at=at - timedelta(seconds=30),
                       finished_at=None if status in (QUEUED, RUNNING) else at, error=error, stats=stats))
        await db.commit()


async def add_bank(sessions, user_id, status="active"):
    async with sessions() as db:
        db.add(BankConnection(user_id=user_id, provider="tbank", token_encrypted=encrypt("bank-token"), status=status))
        await db.commit()


def kinds_of(no_celery) -> list[str]:
    return [args[0] for args in no_celery]


# ── «Обновить всё» ──
async def test_sync_all_queues_only_idle_and_not_recent(client, tokens, sessions, no_celery):
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    await add_run(sessions, uid, "products", SUCCESS, ago=timedelta(minutes=2))  # только что — «недавно»
    await add_run(sessions, uid, "stocks", RUNNING)  # уже идёт
    await add_run(sessions, uid, "finance", SUCCESS, ago=timedelta(hours=3))  # давно — обновим

    r = await client.post(f"{API}/all", headers=auth(tokens))
    assert r.status_code == 202
    items = {i["kind"]: i for i in r.json()["items"]}
    assert {k: i["result"] for k, i in items.items()} == {
        "products": "recent", "stocks": "running", "promotions": "queued", "finance": "queued", "balance": "queued",
        "postings": "queued", "supplies": "queued",
    }
    assert items["products"]["recent_until"] is not None and items["promotions"]["run_id"]
    assert kinds_of(no_celery) == ["promotions", "finance", "balance", "postings", "supplies"]  # идущие и свежие — нет
    message = r.json()["message"]
    assert "Обновляем: акции, начисления, баланс и выплаты, отправления, заявки на поставку." in message
    assert "Уже загружаются: остатки." in message and "Обновлены недавно: товары и цены — снова можно через" in message

    # второй раз: поставленные уже в очереди — ничего нового
    r = await client.post(f"{API}/all", headers=auth(tokens))
    assert {i["result"] for i in r.json()["items"]} == {"recent", "running"}
    assert len(no_celery) == 5


async def test_sync_all_one_kind_and_checks(client, tokens, sessions, no_celery):
    uid = await me(client, tokens)
    r = await client.post(f"{API}/all", headers=auth(tokens))
    assert r.status_code == 400 and "API-ключ" in r.json()["detail"]  # нечего обновлять

    await add_key(sessions, uid)
    r = await client.post(f"{API}/all", json={"kinds": ["promotions"]}, headers=auth(tokens))
    assert r.status_code == 202 and [i["kind"] for i in r.json()["items"]] == ["promotions"]
    assert kinds_of(no_celery) == ["promotions"]

    assert (await client.post(f"{API}/all", json={"kinds": ["anything"]}, headers=auth(tokens))).status_code == 400
    assert (await client.post(f"{API}/all", json={"kinds": []}, headers=auth(tokens))).status_code == 400
    r = await client.post(f"{API}/all", json={"kinds": ["bank"]}, headers=auth(tokens))
    assert r.status_code == 400 and "Банк не подключён" in r.json()["detail"]


async def test_sync_all_bank_without_ozon_key(client, tokens, sessions, no_celery):
    uid = await me(client, tokens)
    await add_bank(sessions, uid)
    r = await client.post(f"{API}/all", headers=auth(tokens))
    assert r.status_code == 202 and [(i["kind"], i["result"]) for i in r.json()["items"]] == [("bank", "queued")]
    r = await client.post(f"{API}/all", json={"kinds": ["products"]}, headers=auth(tokens))
    assert r.status_code == 400 and "API-ключ" in r.json()["detail"]


async def test_sync_all_queue_down(client, tokens, sessions, monkeypatch):
    from app.api.v1 import sync as sync_api

    uid = await me(client, tokens)
    await add_key(sessions, uid)

    def down(*_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(sync_api, "enqueue_sync", down)
    r = await client.post(f"{API}/all", json={"kinds": ["stocks"]}, headers=auth(tokens))
    assert r.json()["items"][0]["result"] == "unavailable" and "Не удалось поставить в очередь: остатки" in r.json()["message"]
    stocks = next(k for k in (await client.get(f"{API}/status", headers=auth(tokens))).json()["kinds"] if k["kind"] == "stocks")
    assert stocks["state"] == "error" and stocks["problem"] == "Фоновые задачи были недоступны"


async def test_sync_all_store_scoped(client, tokens, sessions, no_celery):
    # «Обновить всё» — только текущий магазин (правило 17): у второго магазина ключа нет
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    async with sessions() as db:
        from app.models.user import User

        store = User(owner_id=uid, store_name="Второй")
        db.add(store)
        await db.commit()
        store_id = store.id
    r = await client.post(f"{API}/all", headers={**auth(tokens), "X-Store-Id": str(store_id)})
    assert r.status_code == 400 and no_celery == []
    r = await client.post(f"{API}/all", headers=auth(tokens))
    assert r.status_code == 202 and {args[1] for args in no_celery} == {uid}


# ── статус по видам ──
async def test_status_without_sources(client, tokens):
    s = (await client.get(f"{API}/status", headers=auth(tokens))).json()
    assert s["state"] == "no_source" and s["kinds"] == [] and s["oldest_kind"] is None


async def test_status_per_kind(client, tokens, sessions):
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    await add_run(sessions, uid, "products", SUCCESS, ago=timedelta(minutes=5))
    await add_run(sessions, uid, "stocks", SUCCESS, ago=timedelta(hours=2))
    await add_run(sessions, uid, "stocks", FAILED, ago=timedelta(minutes=3), error="Ozon не принял API-ключ: неверный "
                  "Client-Id или Api-Key", stats={"error_kind": runner.KEY})
    await add_run(sessions, uid, "finance", SUCCESS, ago=timedelta(hours=20))  # раз в 6 часов → давно
    await add_run(sessions, uid, "promotions", RUNNING)
    # старый провал без error_kind (до шага 10.2) — причина по тексту
    await add_run(sessions, uid, "postings", FAILED, error="Ozon временно не отвечает (ошибка на стороне Ozon), попробуйте позже")

    s = (await client.get(f"{API}/status", headers=auth(tokens))).json()
    k = {x["kind"]: x for x in s["kinds"]}
    assert list(k) == ["products", "stocks", "promotions", "finance", "balance", "postings", "supplies"]
    assert k["products"]["state"] == "fresh" and k["products"]["problem"] is None
    assert k["products"]["recent_until"] is not None and k["products"]["schedule"] == "сами — раз в час"
    assert k["stocks"]["state"] == "error" and k["stocks"]["problem"].startswith("Ozon не принял ключ")
    assert k["stocks"]["action"] == {"label": "Заменить ключ", "href": "/dashboard/settings/integrations"}
    assert k["stocks"]["last_success_at"] is not None and "неверный Client-Id" in k["stocks"]["error"]
    assert k["stocks"]["can_refresh"] is True  # ключ есть — после замены можно обновить сразу
    assert k["finance"]["state"] == "stale" and k["finance"]["problem"] == "Давно не обновлялись"
    assert k["promotions"]["state"] == "running" and k["promotions"]["running"] is True
    assert k["balance"]["state"] == "never" and k["balance"]["last_success_at"] is None
    assert k["postings"]["problem"] == "Ozon временно не отвечает" and k["postings"]["action"] is None
    assert s["state"] == "running"  # идёт загрузка — главное для кнопки
    assert s["oldest_kind"] == "balance" and s["oldest_success_at"] is None  # ещё не загружался — самые старые данные
    # прежние поля статуса — на месте (страницы разделов)
    assert s["products"]["last_success_at"] is not None and s["has_active_key"] is True

    async with sessions() as db:  # загрузка акций закончилась — остаётся ошибка остатков
        run = (await db.scalars(select(SyncRun).where(SyncRun.kind == "promotions"))).one()
        run.status, run.finished_at = SUCCESS, now()
        await db.commit()
    assert (await client.get(f"{API}/status", headers=auth(tokens))).json()["state"] == "error"


async def test_status_all_fresh_and_oldest(client, tokens, sessions):
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    for kind, ago in (("products", 20), ("stocks", 5), ("promotions", 30), ("finance", 90), ("balance", 60), ("postings", 45),
                      ("supplies", 25)):
        await add_run(sessions, uid, kind, SUCCESS, ago=timedelta(minutes=ago))
    s = (await client.get(f"{API}/status", headers=auth(tokens))).json()
    assert s["state"] == "fresh" and s["oldest_kind"] == "finance"
    assert {x["state"] for x in s["kinds"]} == {"fresh"}


async def test_status_expired_key_and_bank_token(client, tokens, sessions):
    uid = await me(client, tokens)
    await add_key(sessions, uid, expires=-timedelta(days=1))  # ключ был, но истёк
    await add_bank(sessions, uid, status="auth")  # банку нужен новый токен
    s = (await client.get(f"{API}/status", headers=auth(tokens))).json()
    k = {x["kind"]: x for x in s["kinds"]}
    assert s["state"] == "error" and s["has_active_key"] is False and "bank" in k
    assert k["products"]["problem"] == "Ключ Ozon не подключён или истёк"
    assert k["products"]["action"]["href"] == "/dashboard/settings/integrations" and k["products"]["can_refresh"] is False
    assert k["bank"]["problem"] == "Банк не принял токен"
    assert k["bank"]["action"] == {"label": "Заменить токен", "href": "/dashboard/settings/integrations?tab=banks"}
    assert k["bank"]["can_refresh"] is False


async def test_stale_run_marked_interrupted(client, tokens, sessions):
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    await add_run(sessions, uid, "stocks", RUNNING, ago=timedelta(hours=1))  # воркер упал
    k = {x["kind"]: x for x in (await client.get(f"{API}/status", headers=auth(tokens))).json()["kinds"]}
    assert k["stocks"]["state"] == "error" and k["stocks"]["problem"] == "Загрузка прервалась"


# ── причина провала записывается при запуске ──
@pytest.mark.parametrize("exc, kind", [
    (OzonAuthError("нет роли", kind="no_role"), runner.KEY_ROLE),
    (OzonAuthError("неверный ключ"), runner.KEY),
    (OzonRateLimitError(), runner.RATE_LIMIT),
    (OzonServerError(), runner.OZON_DOWN),
    (RuntimeError("boom"), runner.INTERNAL),
])
async def test_runner_records_error_kind(sessions, monkeypatch, client, tokens, exc, kind):
    uid = await me(client, tokens)
    await add_key(sessions, uid)

    async def job(*_a, **_k):
        raise exc

    monkeypatch.setitem(runner.JOBS, "products", job)
    run = await runner.run_sync("products", uid, sessions)
    assert run.status == FAILED and run.stats == {"error_kind": kind}
    assert overview.PROBLEMS[kind].text  # у каждой причины — объяснение


async def test_runner_no_key(sessions, client, tokens):
    run = await runner.run_sync("products", await me(client, tokens), sessions)
    assert run.status == FAILED and run.stats == {"error_kind": runner.NO_KEY}


# ── ошибки без секретов ──
async def test_errors_without_secrets(sessions, client, tokens):
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        mock.post(url__regex=r".*").mock(return_value=httpx.Response(
            400, json={"code": 3, "message": f"invalid request for key {API_KEY}"}))
        run = await runner.run_sync("products", uid, sessions)
    assert run.status == FAILED and API_KEY not in (run.error or "") and "***" in run.error
    body = (await client.get(f"{API}/status", headers=auth(tokens))).text
    assert API_KEY not in body and "bank-token" not in body
    products = next(k for k in json.loads(body)["kinds"] if k["kind"] == "products")
    assert products["problem"] == "Ozon отклонил запрос" and "***" in products["error"]


# ── чистые функции ──
def test_kind_state_order():
    t = now()
    ok = SyncRun(kind="products", status=SUCCESS, finished_at=t - timedelta(minutes=1))
    running = SyncRun(kind="products", status=RUNNING)
    assert overview.kind_state("products", running, ok.finished_at, t).state == "running"
    assert overview.kind_state("products", ok, ok.finished_at, t).recent_until == ok.finished_at + overview.RECENT["products"]
    assert overview.kind_state("products", ok, t - timedelta(minutes=30), t).recent_until is None
    assert overview.kind_state("products", None, None, t).state == "never"
    blocked = overview.PROBLEMS[runner.NO_KEY]
    assert overview.kind_state("products", ok, ok.finished_at, t, blocked).state == "error"
    assert overview.overall([]) == "no_source"
