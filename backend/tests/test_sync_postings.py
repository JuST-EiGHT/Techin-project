"""Отправления FBO и FBS (шаг 5.2): синхронизация на записанных ответах Ozon, без персональных данных, API, Celery."""
import copy
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import settings
from app.integrations.ozon.client import OzonClient
from app.models.posting import Posting, PostingSync
from app.models.sync_run import SUCCESS
from app.models.warehouse import Warehouse
from app.services.sync import postings as sync_mod
from app.services.sync import runner
from app.services.sync.finance import MSK, today_msk
from app.tasks import sync as tasks
from tests.test_sync_stocks import API_KEY, FBS_WH, auth, make_user

FIXTURES = Path(__file__).parent / "fixtures" / "ozon"
P1, P2, P3, F1 = "0101010101-0001-1", "0101010101-0002-1", "0101010101-0003-1", "0208245774-0029-1"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeOzon:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.fbo = load("posting_fbo_list")
        self.fbs = load("posting_fbs_list")
        self.fbo_pages: dict[str, dict] | None = None
        self.calls: dict[str, list[dict]] = {"fbo": [], "fbs": []}
        mock.post("/v3/posting/fbo/list").mock(side_effect=self._fbo)
        mock.post("/v4/posting/fbs/list").mock(side_effect=self._fbs)

    def _fbo(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls["fbo"].append(body)
        if self.fbo_pages is not None:
            return httpx.Response(200, json=self.fbo_pages[body["cursor"]])
        return httpx.Response(200, json=self.fbo)

    def _fbs(self, request: httpx.Request) -> httpx.Response:
        self.calls["fbs"].append(json.loads(request.content))
        return httpx.Response(200, json=self.fbs)

    def fbo_posting(self, number: str) -> dict:
        return next(p for p in self.fbo["postings"] if p["posting_number"] == number)


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeOzon(mock)


async def add_own_warehouse(sessions, user_id, ozon_id=FBS_WH, kind="FBS") -> None:
    async with sessions() as db:
        db.add(Warehouse(user_id=user_id, ozon_warehouse_id=ozon_id, name="Склад Подольск", type=kind,
                         is_active=True, synced_at=datetime.now(timezone.utc)))
        await db.commit()


async def sync(sessions, user_id) -> sync_mod.PostingSyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_postings(db, user_id, client)


async def postings(sessions, user_id) -> dict[str, Posting]:
    async with sessions() as db:
        res = await db.execute(select(Posting).where(Posting.user_id == user_id))
        return {p.posting_number: p for p in res.scalars()}


def since_of(days_back: int) -> str:
    start = datetime.combine(today_msk() - timedelta(days=days_back - 1), time(), MSK).astimezone(timezone.utc)
    return start.isoformat().replace("+00:00", "Z")


# ── синхронизация ──
async def test_first_sync(sessions, ozon):
    user_id = await make_user(sessions)
    await add_own_warehouse(sessions, user_id)
    stats = await sync(sessions, user_id)
    assert stats.as_dict() == {
        "fbo_new": 3, "fbo_updated": 0, "fbo_unchanged": 0, "fbs_new": 1, "fbs_updated": 0, "fbs_unchanged": 0,
        "skipped": 0, "unknown_products": 1,
    }
    # первый раз — за 90 дней, по 100, только аналитика и финансы (без юрлица покупателя)
    (body,) = ozon.calls["fbo"]
    assert body["filter"]["since"] == since_of(90) and body["limit"] == 100 and body["cursor"] == ""
    assert body["with"] == {"analytics_data": True, "financial_data": True}
    assert ozon.calls["fbs"][0]["filter"]["since"] == since_of(90)

    saved = await postings(sessions, user_id)
    p1 = saved[P1]
    assert (p1.scheme, p1.status, p1.day, p1.warehouse_id, p1.warehouse_name, p1.cluster_from, p1.cluster_to) == (
        "FBO", "delivered", date(2026, 10, 10), 1020000000000101, "ЕКАТЕРИНБУРГ_РФЦ_НОВЫЙ", "Екатеринбург", "Екатеринбург",
    )
    assert [(i.sku, i.offer_id, i.quantity, i.price, i.product_id is not None) for i in p1.items] == [
        (900001001, "6001-2RS.C3.SRL.NAC.TH", 2, Decimal("1290.00"), True),
    ]
    assert saved[P2].day == date(2026, 10, 12)  # 21:30 UTC — уже 12-е по Москве
    assert saved[P2].status == "cancelled"
    p3 = saved[P3]
    assert p3.cluster_to == "Новосибирск" and [i.product_id is None for i in p3.items] == [False, True]  # чужой товар
    f1 = saved[F1]
    assert (f1.scheme, f1.warehouse_id, f1.day, f1.status) == ("FBS", FBS_WH, date(2026, 10, 12), "awaiting_packaging")

    async with sessions() as db:
        states = {s.scheme: s.loaded_from for s in (await db.execute(select(PostingSync))).scalars()}
    assert states == {"FBO": today_msk() - timedelta(days=89), "FBS": today_msk() - timedelta(days=89)}


async def test_no_personal_data_stored(sessions, ozon):
    user_id = await make_user(sessions)
    await add_own_warehouse(sessions, user_id)
    await sync(sessions, user_id)
    saved = await postings(sessions, user_id)
    text = json.dumps([p.raw_json for p in saved.values()], ensure_ascii=False)
    for secret in ("Иван Петров", "+79031234567", "ivan.petrov@example.com", "кв. 78", "ООО Покупатель", "7701234567",
                   "SECRET-CODE-1", "123456789012345", "Московская область", "TRK1234567890"):
        assert secret not in text, secret
    raw = saved[F1].raw_json
    assert "customer" not in raw and "addressee" not in raw and "legal_info" not in raw and "barcodes" not in raw
    assert raw["products"] == [{"is_marketplace_buyout": False, "name": "Подшипник 6001-2RS NACHI",
                                "offer_id": "6001-2RS.C3.SRL.NAC.TH", "price": {"amount": "1290.00", "currency": "RUB"},
                                "quantity": 1, "sku": 900001001}]
    assert saved[P1].raw_json["analytics_data"]["warehouse_id"] == 1020000000000101
    assert "city" not in saved[P1].raw_json["analytics_data"]
    assert saved[P3].raw_json["financial_data"]["cluster_to"] == "Новосибирск"  # кластер нужен, город — нет


async def test_resync_refreshes_35_days_and_updates_status(sessions, ozon):
    user_id = await make_user(sessions)
    await sync(sessions, user_id)
    stats = await sync(sessions, user_id)
    assert (stats.fbo_new, stats.fbo_updated, stats.fbo_unchanged) == (0, 0, 3)
    assert ozon.calls["fbo"][-1]["filter"]["since"] == since_of(35)

    p3 = ozon.fbo_posting(P3)
    p3["status"] = "cancelled"
    p3["products"] = p3["products"][:1]
    stats = await sync(sessions, user_id)
    assert (stats.fbo_updated, stats.fbo_unchanged) == (1, 2)
    saved = (await postings(sessions, user_id))[P3]
    assert saved.status == "cancelled" and [i.sku for i in saved.items] == [900001001]
    async with sessions() as db:
        state = await db.get(PostingSync, (user_id, "FBO"))
        assert state.loaded_from == today_msk() - timedelta(days=89)  # загружено с — не сдвигается вперёд
        assert await db.scalar(select(func.count()).select_from(Posting)) == 3  # отправления не удаляются


async def test_fbs_only_with_own_warehouses(sessions, ozon):
    user_id = await make_user(sessions)
    stats = await sync(sessions, user_id)
    assert ozon.calls["fbs"] == [] and stats.fbs_new == 0
    async with sessions() as db:
        assert await db.get(PostingSync, (user_id, "FBS")) is None
    await add_own_warehouse(sessions, user_id, ozon_id=20605650763000, kind="rFBS")
    assert (await sync(sessions, user_id)).fbs_new == 1


async def test_pagination_and_bad_rows(sessions, ozon):
    user_id = await make_user(sessions)
    first, second = copy.deepcopy(ozon.fbo), copy.deepcopy(ozon.fbo)
    first["postings"], first["has_next"], first["cursor"] = first["postings"][:2], True, "c2"
    second["postings"] = second["postings"][2:] + [{"posting_number": "", "created_at": "2026-10-11T22:10:00Z"},
                                                   {"posting_number": "X-1", "created_at": "не дата"}]
    ozon.fbo_pages = {"": first, "c2": second}
    stats = await sync(sessions, user_id)
    assert [c["cursor"] for c in ozon.calls["fbo"]] == ["", "c2"]
    assert (stats.fbo_new, stats.skipped) == (3, 2)


async def test_run_sync_postings_kind(sessions, ozon):
    user_id = await make_user(sessions)
    run = await runner.run_sync("postings", user_id, sessions)
    assert run.status == SUCCESS and run.kind == "postings" and run.stats["fbo_new"] == 3


# ── API ──
async def test_api_manual_postings_sync_and_status(sessions, client, tokens, ozon, no_celery):
    r = await client.post("/api/v1/sync/postings", headers=auth(tokens))
    assert r.status_code == 400  # нет ключа
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    from app.models.api_credential import ApiCredential
    from app.services.encryption import encrypt

    async with sessions() as db:
        db.add(ApiCredential(user_id=uuid.UUID(me["id"]), label="k", client_id="1234567",
                             api_key_encrypted=encrypt(API_KEY), is_active=True,
                             expires_at=datetime.now(timezone.utc) + timedelta(days=60)))
        await db.commit()
    r = await client.post("/api/v1/sync/postings", headers=auth(tokens))
    assert r.status_code == 202 and r.json()["kind"] == "postings" and no_celery[-1][0] == "postings"
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["postings"]["last_run"]["status"] == "queued"


# ── Celery ──
def test_beat_postings_every_6_hours():
    from app.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sync-postings-every-6h"]
    assert entry["task"] == "tasks.sync_postings_all"
    assert entry["schedule"].minute == {38} and entry["schedule"].hour == {0, 6, 12, 18}
    assert tasks.FOR_USER_TASKS["postings"] is tasks.sync_postings_for_user


def test_postings_fanout(monkeypatch):
    users = [uuid.uuid4()]

    async def fake_users():
        return users

    queued = []
    monkeypatch.setattr(tasks, "_active_users", fake_users)
    monkeypatch.setattr(tasks.sync_postings_for_user, "delay", lambda uid: queued.append(uid))
    assert tasks.sync_postings_all() == 1 and queued == [str(users[0])]
