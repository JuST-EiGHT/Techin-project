"""Заявки покупателей на скидку (шаг 4.3a): синхронизация и история статусов, расчёт и пометки, предпросмотр →
применение ровно один раз, журнал, ошибки Ozon по каждой заявке, уведомления, лента событий, разделение магазинов."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import settings
from app.engine import anomaly_detector as det
from app.engine import discounts as engine
from app.integrations.ozon.client import OzonClient
from app.models.anomaly import Anomaly
from app.models.audit import AuditLog
from app.models.discount import DiscountDecision, DiscountTask, DiscountTaskEvent
from app.models.product import Product
from app.models.user import User
from app.services import anomalies
from app.services.sync import discount_tasks as sync_mod
from app.services.sync.finance import today_msk
from tests.test_pricing import add_sales
from tests.test_sync_promotions import API_KEY, auth, setup_user

LIST = "/v2/actions/discounts-task/list"
APPROVE = "/v1/actions/discounts-task/approve"
DECLINE = "/v1/actions/discounts-task/decline"
URL = "/api/v1/discounts"
SKU = {1001: 900001001, 1002: 900001002, 1003: 900001003}
OFFER = {1001: "6001-2RS.C3.SRL.NAC.TH", 1003: "6000-2RS.C3.SRL.NAC.JP"}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def task(tid: int, sku: int, price: str, *, status: str = "NEW", hours: float = 48, qty: int = 2, **extra) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": tid, "sku": sku, "name": f"Подшипник {sku}", "status": status, "created_at": iso(now - timedelta(hours=1)),
        "end_at": iso(now + timedelta(hours=hours)), "edited_till": iso(now + timedelta(hours=hours)),
        "requested_price": float(price), "requested_discount": 10, "requested_quantity_max": qty,
        "original_price": 600, "min_auto_price": 0, "approved_price": 0, "approved_discount": 0,
        "approved_quantity_max": 0, "is_auto_moderated": False, "moderated_at": "0001-01-01T00:00:00Z",
        "email": "manager@shop.example", "first_name": "Иван", "last_name": "Петров", "patronymic": "",
        "auto_moderated_info": {"max_percent": 0, "max_price": 0, "min_percent": 0, "min_price": 0},
        **extra,
    }


class FakeOzon:
    """Фейковый Ozon: список заявок (страницами по last_id), одобрение и отклонение с ответом по каждой заявке."""

    def __init__(self, mock: respx.MockRouter) -> None:
        self.tasks: list[dict] = []
        self.lists: list[dict] = []
        self.writes: list[tuple[str, dict]] = []
        self.fail: dict[int, str] = {}  # id заявки → error_for_user
        self.error: int | None = None  # HTTP-ошибка на запись
        self.timeout = False
        mock.post(LIST).mock(side_effect=self._list)
        mock.post(APPROVE).mock(side_effect=lambda r: self._write(APPROVE, r))
        mock.post(DECLINE).mock(side_effect=lambda r: self._write(DECLINE, r))

    def _list(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.lists.append(body)
        items = sorted(self.tasks, key=lambda t: t["id"])
        if body.get("last_id"):
            items = [t for t in items if t["id"] > body["last_id"]]
        return httpx.Response(200, json={"tasks": items[: body["limit"]]})

    def _write(self, path: str, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.writes.append((path, body))
        if self.timeout:
            raise httpx.ReadTimeout("timeout", request=request)
        if self.error:
            return httpx.Response(self.error, json={"code": 3, "message": "internal error"})
        ids = [t["id"] for t in body["tasks"]]
        fails = [{"task_id": i, "error_for_user": self.fail[i]} for i in ids if i in self.fail]
        return httpx.Response(200, json={"result": {"fail_details": fails, "success_count": len(ids) - len(fails),
                                                    "fail_count": len(fails)}})


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeOzon(mock)


async def sync(sessions, user_id) -> sync_mod.DiscountSyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_discount_tasks(db, user_id, client)


async def ready(sessions, client, tokens) -> uuid.UUID:
    """Магазин из API: товары 1001–1003 по 540 ₽ (комиссия 38 %, эквайринг 1,5 %), у 1001 и 1003 — себестоимость
    164 ₽ × 1,22 и продажа с логистикой 30 ₽; на складе 1001 — 5 шт. (остатков нет — 0)."""
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    user_id = await setup_user(sessions, uuid.UUID(me["id"]))
    for pid in (1001, 1003):
        await client.put(f"/api/v1/costs/{OFFER[pid]}", json={"cost_price": "164", "vat_multiplier": "1.22"},
                         headers=auth(tokens))
    async with sessions() as db:
        products = {p.ozon_product_id: p for p in (await db.execute(
            select(Product).where(Product.user_id == user_id))).scalars()}
        await add_sales(db, user_id, products[1001], today_msk() - timedelta(days=1), logistics=Decimal("30"))
        await db.commit()
    return user_id


async def breakeven(client, tokens, product_id) -> Decimal:
    r = await client.post("/api/v1/price/calculate", json={"product_id": str(product_id)}, headers=auth(tokens))
    return Decimal(r.json()["result"]["min_price"])


async def product_id(sessions, user_id, ozon_pid) -> uuid.UUID:
    async with sessions() as db:
        return (await db.execute(select(Product.id).where(Product.user_id == user_id,
                                                           Product.ozon_product_id == ozon_pid))).scalar_one()


async def task_id(sessions, user_id, ozon_tid) -> uuid.UUID:
    async with sessions() as db:
        return (await db.execute(select(DiscountTask.id).where(DiscountTask.user_id == user_id,
                                                                DiscountTask.ozon_task_id == ozon_tid))).scalar_one()


# ── чистые расчёты ──
def test_state_marks_and_rule():
    now = datetime.now(timezone.utc)
    assert engine.state("NEW", now + timedelta(hours=1), now) == engine.NEW
    assert engine.state("NEW", now - timedelta(minutes=1), now) == engine.EXPIRED  # «истекла» — не статус Ozon
    assert engine.state("APPROVED", now - timedelta(days=1), now) == engine.APPROVED
    assert engine.hours_left(now + timedelta(minutes=90), now) == Decimal("1.5")
    assert engine.discount_pct(Decimal(500), Decimal(450)) == Decimal("10.0")
    low = Decimal(10)
    assert engine.marks(Decimal(300), Decimal(380), None, Decimal(-5), low) == [engine.LOSS]
    assert engine.marks(Decimal(420), Decimal(380), Decimal(450), Decimal("5.7"), low) == [
        engine.BELOW_OZON_MIN, engine.LOW_MARGIN]
    assert engine.marks(Decimal(500), Decimal(380), Decimal(450), Decimal(15), low) == []
    assert engine.marks(None, Decimal(380), None, None, low) == []  # нет цены — нет пометок, не «в минус»
    assert engine.rule_ok(Decimal(15), Decimal(12)) is True and engine.rule_ok(Decimal(5), Decimal(12)) is False
    assert engine.rule_ok(Decimal(15), None) is None and engine.rule_ok(None, Decimal(12)) is None


def test_parse_strips_personal_data():
    p = sync_mod.parse(task(7, 900001001, "450.5", qty=3, approved_price=0, min_auto_price=420))
    assert (p.ozon_task_id, p.sku, p.status, p.requested_price, p.requested_quantity_max) == (
        7, 900001001, "NEW", Decimal("450.50"), 3)
    assert p.approved_price is None and p.moderated_at is None  # 0 и «0001-01-01» — нет значения, а не 0
    assert p.min_auto_price == Decimal("420.00") and p.auto_moderated_info is None
    assert not {"email", "first_name", "last_name", "patronymic"} & set(p.raw)  # сотрудника не храним
    assert sync_mod.parse({"sku": 1}) is None


# ── синхронизация ──
async def test_sync_pages_history_and_unknown_product(sessions, ozon):
    user_id = await setup_user(sessions)
    ozon.tasks = [task(i, SKU[1001], "450") for i in range(1, 56)] + [task(99, 123456, "100")]  # 56 заявок, 1 — чужой SKU
    stats = await sync(sessions, user_id)
    assert (stats.tasks, stats.new, stats.status_changes, stats.unknown_products) == (56, 56, 0, 1)
    assert [b.get("last_id") for b in ozon.lists] == [None, 50] and ozon.lists[0]["status"] == "ALL"
    assert ozon.lists[0]["limit"] == 50

    # повтор без изменений — ни новых заявок, ни событий
    stats = await sync(sessions, user_id)
    assert (stats.new, stats.status_changes) == (0, 0)
    # заявку одобрили в кабинете Ozon — новое событие, прежнее остаётся
    ozon.tasks[0].update(status="APPROVED", approved_price=470, approved_quantity_max=1,
                         moderated_at=iso(datetime.now(timezone.utc)))
    stats = await sync(sessions, user_id)
    assert stats.status_changes == 1
    async with sessions() as db:
        t = (await db.execute(select(DiscountTask).where(DiscountTask.ozon_task_id == 1))).scalar_one()
        events = list((await db.execute(select(DiscountTaskEvent).where(DiscountTaskEvent.task_id == t.id)
                                        .order_by(DiscountTaskEvent.seen_at))).scalars())
        assert (t.status, t.approved_price, t.approved_quantity_max) == ("APPROVED", Decimal("470.00"), 1)
        assert [(e.status, e.source) for e in events] == [("NEW", "ozon"), ("APPROVED", "ozon")]
        assert "email" not in t.raw_json and "first_name" not in t.raw_json
        other = (await db.execute(select(DiscountTask).where(DiscountTask.ozon_task_id == 99))).scalar_one()
        assert other.product_id is None  # товара нет в каталоге — заявка всё равно сохранена


async def test_sync_kind_registered(sessions, ozon):
    """Новый вид — в журнале запусков и окне «Синхронизация» (10.2)."""
    from app.services.sync import overview, runner
    from app.tasks import sync as tasks

    assert "discounts" in runner.JOBS and "discounts" in tasks.FOR_USER_TASKS and "discounts" in overview.OZON_KINDS
    assert overview.TITLES["discounts"] == "Заявки на скидку"
    from app.tasks.celery_app import celery_app

    assert celery_app.conf.beat_schedule["sync-discounts-every-30-min"]["task"] == "tasks.sync_discounts_all"


async def test_sync_failure_is_a_warning(sessions):
    """Ozon отказал — запуск с ошибкой и причиной, а не падение."""
    from app.models.sync_run import FAILED
    from app.services.sync import runner

    user_id = await setup_user(sessions)
    with respx.mock(base_url=settings.OZON_API_URL) as mock:
        mock.post(LIST).mock(return_value=httpx.Response(403, json={"code": 7, "message": "Api-key has no role"}))
        run = await runner.run_sync("discounts", user_id, sessions)
    assert run.status == FAILED and run.stats["error_kind"] in (runner.KEY_ROLE, runner.KEY)


# ── расчёт и список ──
async def test_list_calculation_marks_tabs_and_search(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    pid = await product_id(sessions, user_id, 1001)
    be = await breakeven(client, tokens, pid)
    assert Decimal(300) < be < Decimal(420)
    now = datetime.now(timezone.utc)
    ozon.tasks = [
        task(1, SKU[1001], "500"),  # выгодно
        task(2, SKU[1001], "420", min_auto_price=450),  # маржа ниже порога и ниже мин. цены Ozon
        task(3, SKU[1001], "300", hours=5),  # в минус и скоро истечёт
        task(4, SKU[1002], "480"),  # без себестоимости — расчёта нет
        task(5, SKU[1003], "470", status="APPROVED", approved_price=490, approved_quantity_max=1,
             moderated_at=iso(now)),
        task(6, SKU[1003], "470", hours=-1),  # срок прошёл — «истекла»
        task(7, SKU[1003], "470", status="DECLINED"),
    ]
    await sync(sessions, user_id)
    r = await client.get(URL, headers=auth(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"new": 4, "approved": 1, "declined": 1, "expired": 1, "all": 7, "loss": 1}
    rows = {x["ozon_task_id"]: x for x in body["items"]}
    assert list(rows)[0] == 3  # срочные — первыми
    assert set(rows) == {1, 2, 3, 4}
    assert Decimal(rows[1]["breakeven_price"]) == be  # минимальная для участия — как в калькуляторе цены
    assert rows[1]["marks"] == [] and Decimal(rows[1]["margin"]) > 10 and Decimal(rows[1]["unit_profit"]) > 0
    assert Decimal(rows[1]["total_profit"]) == Decimal(rows[1]["unit_profit"]) * 2
    assert rows[2]["marks"] == ["below_ozon_min", "low_margin"]
    assert rows[3]["marks"] == ["loss"] and Decimal(rows[3]["unit_profit"]) < 0
    assert Decimal(rows[3]["hours_left"]) <= 5
    assert rows[4]["margin"] is None and rows[4]["marks"] == [] and rows[4]["missing"]  # нет данных ≠ 0
    assert rows[1]["price"] == "540.00" and Decimal(rows[1]["discount_pct"]) == Decimal("7.4")
    assert rows[1]["product"]["offer_id"] == OFFER[1001] and rows[1]["can_decide"] is True

    expired = (await client.get(URL, params={"tab": "expired"}, headers=auth(tokens))).json()["items"]
    assert [x["ozon_task_id"] for x in expired] == [6] and expired[0]["can_decide"] is False
    approved = (await client.get(URL, params={"tab": "approved"}, headers=auth(tokens))).json()["items"]
    assert approved[0]["approved_price"] == "490.00" and approved[0]["approved_margin"] is not None
    found = (await client.get(URL, params={"tab": "all", "search": "6000-2rs"}, headers=auth(tokens))).json()
    assert {x["ozon_task_id"] for x in found["items"]} == {5, 6, 7} and found["counts"]["all"] == 3
    loss = (await client.get(URL, params={"marks": "loss"}, headers=auth(tokens))).json()["items"]
    assert [x["ozon_task_id"] for x in loss] == [3]
    by_price = (await client.get(URL, params={"sort": "requested_price", "order": "asc"}, headers=auth(tokens))).json()
    assert [x["ozon_task_id"] for x in by_price["items"]] == [3, 2, 4, 1]

    # подсказка «одобрили бы по правилу» — порог пользователя
    assert rows[1]["rule_ok"] is None
    r = await client.put(f"{URL}/rule", json={"margin_pct": "12"}, headers=auth(tokens))
    assert r.json() == {"margin_pct": "12"}
    rows = {x["ozon_task_id"]: x for x in (await client.get(URL, headers=auth(tokens))).json()["items"]}
    assert (rows[1]["rule_ok"], rows[2]["rule_ok"], rows[4]["rule_ok"]) == (True, False, None)
    await client.put(f"{URL}/rule", json={"margin_pct": None}, headers=auth(tokens))
    assert (await client.get(URL, headers=auth(tokens))).json()["rule_margin_pct"] is None

    x = await client.get(f"{URL}/export", headers=auth(tokens))
    assert x.status_code == 200 and x.content[:2] == b"PK"


# ── решение ──
async def test_approve_preview_apply_once_with_audit(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    ozon.tasks = [task(1, SKU[1001], "500", qty=3)]
    await sync(sessions, user_id)
    tid = await task_id(sessions, user_id, 1)

    r = await client.post(f"{URL}/preview", json={"items": [{"task_id": str(tid), "action": "approve", "price": "510",
                                                             "quantity": 2}], "comment": "Спасибо!"},
                          headers=auth(tokens))
    assert r.status_code == 200
    p = r.json()
    item = p["items"][0]
    assert item["can_apply"] and item["approved_price"] == "510" and item["approved_quantity"] == 2
    assert p["summary"] == {"total": 1, "ok": 1, "blocked": 0, "approve": 1, "decline": 0, "loss": 0,
                            "needs_confirm": False}
    assert ozon.writes == []  # предпросмотр ничего не отправляет

    a = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]},
                          headers={**auth(tokens), "user-agent": "pytest-browser"})
    assert a.status_code == 200, a.text
    res = a.json()
    assert res["applied"] == 1 and res["items"][0]["status"] == "applied" and "одобрено 1" in res["message"]
    assert ozon.writes == [(APPROVE, {"tasks": [{"id": 1, "approved_price": 510.0, "approved_quantity_min": 1,
                                                 "approved_quantity_max": 2, "seller_comment": "Спасибо!"}]})]
    # повтор — не отправится второй раз
    again = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]}, headers=auth(tokens))
    assert again.status_code == 409 and len(ozon.writes) == 1

    async with sessions() as db:
        t = await db.get(DiscountTask, tid)
        assert (t.status, t.approved_price, t.approved_quantity_max) == ("APPROVED", Decimal("510.00"), 2)
        events = list((await db.execute(select(DiscountTaskEvent).where(DiscountTaskEvent.task_id == tid)
                                        .order_by(DiscountTaskEvent.seen_at))).scalars())
        assert [(e.status, e.source) for e in events] == [("NEW", "ozon"), ("APPROVED", "service")]
        log = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "discount_task"))).scalars().all()
        assert len(log) == 1 and log[0].action == "discount.approve" and log[0].status == "applied"
        assert log[0].before_json["status"] == "NEW" and log[0].after_json["approved_price"] == "510.00"
        assert log[0].ozon_request["path"] == APPROVE and log[0].user_agent == "pytest-browser"
        decisions = list((await db.execute(select(DiscountDecision).order_by(DiscountDecision.created_at))).scalars())
        assert [d.status for d in decisions] == ["preview", "applied"] and decisions[1].preview_id == decisions[0].id

    # решённая — больше не решить; лента событий показывает одобрение
    r = await client.post(f"{URL}/preview", json={"items": [{"task_id": str(tid), "action": "decline"}]},
                          headers=auth(tokens))
    assert r.json()["items"][0]["can_apply"] is False and "одобрена" in r.json()["items"][0]["errors"][0]
    feed = (await client.get("/api/v1/analytics/events", params={"kind": "promo"}, headers=auth(tokens))).json()
    assert any(e["title"].startswith("Одобрена заявка на скидку по 510") and e["detail"] == "через сервис"
               for e in feed["items"])
    detail = (await client.get(f"{URL}/{tid}", headers=auth(tokens))).json()
    assert [d["status"] for d in detail["decisions"]] == ["applied"] and len(detail["events"]) == 2


async def test_checks_loss_ack_and_hard_limits(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    ozon.tasks = [task(1, SKU[1001], "300"), task(2, SKU[1002], "480"), task(3, SKU[1001], "500", qty=2)]
    await sync(sessions, user_id)
    t1, t2, t3 = [await task_id(sessions, user_id, i) for i in (1, 2, 3)]

    def body(*items):
        return {"items": [dict(task_id=str(t), action="approve", **kw) for t, kw in items]}

    p = (await client.post(f"{URL}/preview", json=body((t1, {}), (t2, {}), (t3, {"price": "200"}),),
                           headers=auth(tokens))).json()
    items = {i["ozon_task_id"]: i for i in p["items"]}
    assert items[1]["can_apply"] and items[1]["needs_loss_ack"]  # в минус — можно, но только с «понимаю»
    assert any("каждая продажа в минус" in w for w in items[1]["warnings"])
    assert not items[2]["can_apply"] and "Нет себестоимости" in " ".join(items[2]["errors"])  # правило 5
    assert not items[3]["can_apply"] and "ниже той, что просит покупатель" in " ".join(items[3]["errors"])
    assert p["summary"]["ok"] == 1 and p["summary"]["loss"] == 1

    # без «понимаю» — не отправляется, попытка заблокирована и в журнале
    r = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["items"][0]["status"] == "blocked" and ozon.writes == []
    async with sessions() as db:
        assert (await db.execute(select(AuditLog.status).where(AuditLog.entity_type == "discount_task"))).scalar_one() \
            == "rejected"
    # новый предпросмотр и «понимаю» — уходит в Ozon
    p = (await client.post(f"{URL}/preview", json=body((t1, {})), headers=auth(tokens))).json()
    r = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"], "accept_loss": True}, headers=auth(tokens))
    assert r.json()["applied"] == 1 and len(ozon.writes) == 1

    # цена не ниже вашей — не скидка; количество больше запрошенного; ниже себестоимости × 1,05
    p = (await client.post(f"{URL}/preview", json=body((t3, {"price": "540"}),), headers=auth(tokens))).json()
    assert "это не скидка" in p["items"][0]["errors"][0]
    p = (await client.post(f"{URL}/preview", json=body((t3, {"quantity": 5}),), headers=auth(tokens))).json()
    assert "не больше 2 шт." in " ".join(p["items"][0]["errors"])


async def test_many_need_confirmation_and_ozon_errors_per_task(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    ozon.tasks = [task(i, SKU[1001], "500") for i in range(1, 13)]
    await sync(sessions, user_id)
    ids = [await task_id(sessions, user_id, i) for i in range(1, 13)]
    items = [{"task_id": str(t), "action": "decline" if n % 2 else "approve"} for n, t in enumerate(ids)]
    p = (await client.post(f"{URL}/preview", json={"items": items}, headers=auth(tokens))).json()
    assert p["summary"]["needs_confirm"] is True and p["summary"]["ok"] == 12
    r = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]}, headers=auth(tokens))
    assert r.status_code == 400 and "подтвердите" in r.json()["detail"] and ozon.writes == []

    ozon.fail = {2: "Заявка уже обработана"}
    r = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"], "confirmed": True}, headers=auth(tokens))
    res = r.json()
    assert [w[0] for w in ozon.writes] == [APPROVE, DECLINE]  # одобрения и отклонения — своими запросами
    by = {i["ozon_task_id"]: i for i in res["items"]}
    assert by[2]["status"] == "rejected" and "Заявка уже обработана" in by[2]["error"]
    assert res["applied"] == 11 and res["failed"] == 1 and "Не прошло: 1" in res["message"]


async def test_ozon_timeout_and_error_and_expired_preview(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    ozon.tasks = [task(1, SKU[1001], "500"), task(2, SKU[1001], "500")]
    await sync(sessions, user_id)
    t1, t2 = await task_id(sessions, user_id, 1), await task_id(sessions, user_id, 2)

    ozon.timeout = True
    p = (await client.post(f"{URL}/preview", json={"items": [{"task_id": str(t1), "action": "approve"}]},
                           headers=auth(tokens))).json()
    r = (await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]}, headers=auth(tokens))).json()
    assert r["items"][0]["status"] == "unknown" and "могло примениться" in r["items"][0]["error"]
    assert len(ozon.writes) == 1  # без повторов: повтор после таймаута Ozon бы отклонил
    async with sessions() as db:
        assert (await db.get(DiscountTask, t1)).status == "NEW"  # не знаем — не пишем «одобрена»

    ozon.timeout, ozon.error = False, 400
    p = (await client.post(f"{URL}/preview", json={"items": [{"task_id": str(t2), "action": "decline"}]},
                           headers=auth(tokens))).json()
    r = (await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]}, headers=auth(tokens))).json()
    assert r["items"][0]["status"] == "failed" and "ничего не изменилось" in r["items"][0]["error"]

    p = (await client.post(f"{URL}/preview", json={"items": [{"task_id": str(t2), "action": "decline"}]},
                           headers=auth(tokens))).json()
    async with sessions() as db:
        for d in (await db.execute(select(DiscountDecision).where(
                DiscountDecision.batch_id == uuid.UUID(p["batch_id"])))).scalars():
            d.created_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        await db.commit()
    r = await client.post(f"{URL}/apply", json={"batch_id": p["batch_id"]}, headers=auth(tokens))
    assert r.status_code == 410


# ── уведомления ──
async def test_alerts_new_expiring_and_resolved(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    ozon.tasks = [task(1, SKU[1001], "500", hours=48), task(2, SKU[1001], "300", hours=5)]
    await sync(sessions, user_id)
    async with sessions() as db:
        found = await anomalies.detect(db, user_id, only=("discounts",))
    assert found["created"] == 2
    async with sessions() as db:
        rows = {a.key: a for a in (await db.execute(select(Anomaly).where(
            Anomaly.type == det.DISCOUNT_REQUEST, Anomaly.resolved_at.is_(None)))).scalars()}
    assert rows["discount:1"].severity == "yellow" and rows["discount:2"].severity == "red"
    assert rows["discount:2"].money_at_risk is not None  # одобрить в минус — убыток × сколько просят

    alerts = (await client.get("/api/v1/anomalies", params={"section": "discounts"}, headers=auth(tokens))).json()
    titles = [a["title"] for a in alerts["items"]]
    assert any(t.startswith("Заявка на скидку истекает") for t in titles)
    assert all(a["link"].startswith("/dashboard/products/discounts?task=") for a in alerts["items"])

    # заявку решили в кабинете — пункт закрывается
    ozon.tasks[0]["status"] = "DECLINED"
    await sync(sessions, user_id)
    async with sessions() as db:
        await anomalies.detect(db, user_id, only=("discounts",))
        active = {a.key for a in (await db.execute(select(Anomaly).where(
            Anomaly.type == det.DISCOUNT_REQUEST, Anomaly.resolved_at.is_(None)))).scalars()}
    assert active == {"discount:2"}


# ── магазины ──
async def test_other_store_sees_nothing(sessions, client, tokens, ozon):
    user_id = await ready(sessions, client, tokens)
    ozon.tasks = [task(1, SKU[1001], "500")]
    await sync(sessions, user_id)
    tid = await task_id(sessions, user_id, 1)
    other = await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1",
                                                              "full_name": "Другой"})
    assert other.status_code in (200, 201)
    tok = (await client.post("/api/v1/auth/login", json={"email": "other@example.com",
                                                         "password": "strongpass1"})).json()
    assert (await client.get(URL, headers=auth(tok))).json()["counts"]["all"] == 0
    assert (await client.get(f"{URL}/{tid}", headers=auth(tok))).status_code == 404
    r = await client.post(f"{URL}/preview", json={"items": [{"task_id": str(tid), "action": "decline"}]},
                          headers=auth(tok))
    assert r.status_code == 404
    async with sessions() as db:
        assert (await db.execute(select(User).where(User.email == "other@example.com"))).scalar_one() is not None
