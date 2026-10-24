"""Шаг 10.18: заявки на поставку FBO — разбор ответов Ozon (обезличенные фикстуры по форме документации), синхронизация
со статусами и историей, состав и акты приёмки, план против факта."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import settings
from app.engine import supply_orders as engine
from app.integrations.ozon.client import OzonClient
from app.models.supply import ACT, BUNDLE, SupplyOrder, SupplyOrderEvent, SupplyOrderItem
from app.services.sync import supply_orders as sync_mod
from tests.test_sync_stocks import API_KEY, api_user, ozon  # noqa: F401 — фикстура ozon
from tests.test_warehouses import ACB, TH

FIX = Path(__file__).parent / "fixtures" / "ozon" / "supply"


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


# ── разбор ──
def test_parse_order_bundle_act():
    orders = [engine.parse_order(o) for o in load("supply_order_get")["orders"]]
    done, ready, cancelled = orders
    assert (done.order_id, done.number, done.state, done.storage_warehouse) == (
        5001, "2000000005001", "COMPLETED", "СПБ_БУГРЫ_РФЦ")
    assert done.timeslot_from == datetime(2026, 9, 25, 6, tzinfo=timezone.utc) and done.timezone == "Europe/Moscow"
    assert done.supplies[0] == engine.Supply(7001, "bnd-5001", 1020000000000202, "СПБ_БУГРЫ_РФЦ", 2, "COMPLETED")
    assert (ready.state, engine.group_of(ready.state), engine.state_text(ready.state)) == (
        "READY_TO_SUPPLY", engine.PREPARING, "Готова к отгрузке")
    assert cancelled.timeslot_from is None and engine.group_of(cancelled.state) == engine.FAILED
    assert engine.parse_order({"state": "COMPLETED"}) is None  # без номера — пропуск
    assert engine.state_text("NEW_STATE_FROM_OZON") == "New state from ozon"  # новый статус Ozon — как есть

    lines = engine.parse_bundle(load("supply_order_bundles")["bnd-5001"]["items"])
    assert [(line.sku, line.offer_id, line.quantity) for line in lines] == [(900001001, TH, 20), (900001002, ACB, 5)]
    # один SKU разными квантами — одной строкой
    twice = engine.parse_bundle([{"sku": 1, "offer_id": "A", "quantity": 2}, {"sku": 1, "offer_id": "A", "quantity": 3}])
    assert [(line.sku, line.quantity) for line in twice] == [(1, 5)]

    act = engine.parse_act(load("supply_order_act_7001"))
    assert [(line.sku, line.quantity, line.fact, line.approved, line.defect) for line in act] == [
        (900001001, 20, 18, 18, True), (900001002, 5, 5, 5, False)]


def test_plan_vs_fact():
    rows = engine.results({"A": 20, "B": 5}, {"A": 20, "B": 4, "C": 2}, {"A": 18, "B": 4, "C": 2})
    assert [(r.key, r.planned, r.declared, r.accepted, r.diff, r.plan_diff) for r in rows] == [
        ("A", 20, 20, 18, -2, 0), ("B", 5, 4, 4, 0, -1), ("C", None, 2, 2, 0, None)]
    assert engine.totals(rows) == {"planned": 25, "declared": 26, "accepted": 24, "diff": -2, "short": 2}
    # актов ещё нет — «нет данных», а не 0
    rows = engine.results({}, {"A": 20}, None)
    assert (rows[0].accepted, rows[0].diff, engine.totals(rows)["accepted"]) == (None, None, None)


# ── синхронизация ──
class FakeSupply:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.orders = copy.deepcopy(load("supply_order_get"))
        self.bundles = load("supply_order_bundles")
        self.calls: dict[str, list] = {"list": [], "get": [], "bundle": [], "act": [], "clusters": []}
        self.act_status = 200
        mock.post(sync_mod.LIST_PATH).mock(side_effect=self._list)
        mock.post(sync_mod.GET_PATH).mock(side_effect=self._get)
        mock.post(sync_mod.BUNDLE_PATH).mock(side_effect=self._bundle)
        mock.post(sync_mod.ACT_PATH).mock(side_effect=self._act)
        mock.post(sync_mod.CLUSTERS_PATH).mock(side_effect=self._clusters)

    def _list(self, request):
        body = json.loads(request.content)
        self.calls["list"].append(body)
        return httpx.Response(200, json=load("supply_order_list")["pages"][body.get("last_id", "")])

    def _get(self, request):
        ids = {int(i) for i in json.loads(request.content)["order_ids"]}
        self.calls["get"].append(sorted(ids))
        return httpx.Response(200, json={"orders": [o for o in self.orders["orders"] if o["order_id"] in ids]})

    def _bundle(self, request):
        bundle = json.loads(request.content)["bundle_ids"][0]
        self.calls["bundle"].append(bundle)
        return httpx.Response(200, json=self.bundles[bundle])

    def _act(self, request):
        supply = json.loads(request.content)["supply_id"]
        self.calls["act"].append(supply)
        if self.act_status != 200:
            return httpx.Response(self.act_status, json={"message": "beta is down"})
        return httpx.Response(200, json=load("supply_order_act_7001") if supply == 7001 else {"supply_acts": []})

    def _clusters(self, request):
        self.calls["clusters"].append(json.loads(request.content))
        return httpx.Response(200, json=load("cluster_list"))


@pytest.fixture
def supply_api():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeSupply(mock)


async def run_sync(sessions, user_id) -> sync_mod.SupplySyncStats:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await sync_mod.sync_supply_orders(db, user_id, client)


async def test_sync_orders_history_and_items(sessions, client, tokens, ozon, supply_api):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, ozon)
    stats = await run_sync(sessions, user_id)
    assert (stats.orders, stats.fetched, stats.new, stats.state_changes, stats.bundles, stats.acts) == (3, 3, 3, 3, 3, 1)
    assert stats.unknown_products == 1  # UNKNOWN-1 нет в каталоге
    assert supply_api.calls["list"][0]["filter"]["states"] == list(engine.STATES) and supply_api.calls["list"][1]["last_id"] == "p2"
    assert supply_api.calls["act"] == [7001]  # акты — только у принятых
    async with sessions() as db:
        orders = {o.ozon_order_id: o for o in (await db.execute(select(SupplyOrder))).scalars()}
        done = orders[5001]
        # кластер — по нашим складам из остатков; у Краснодара (склада нет в остатках) — по списку кластеров Ozon
        assert (done.state, done.cluster_name, done.storage_warehouse) == ("COMPLETED", "Санкт-Петербург и СЗО", "СПБ_БУГРЫ_РФЦ")
        assert orders[5003].cluster_name == "Краснодар"
        acts = await sync_mod.latest_items(db, done.id, ACT)
        assert sorted((r.offer_id, r.quantity, r.fact, r.defect) for r in acts) == [(TH, 20, 18, True), (ACB, 5, 5, False)]
        assert all(r.product_id is not None for r in acts)

    # второй запуск: итоговые заявки с актами не перечитываем; новый статус и таймслот — новая запись истории
    supply_api.orders["orders"][1]["state"] = "IN_TRANSIT"
    supply_api.orders["orders"][1]["supplies"][0]["state"] = "IN_TRANSIT"
    supply_api.bundles["bnd-5002"]["items"][0]["quantity"] = 10  # состав изменили, пока заполняли
    supply_api.calls = {k: [] for k in supply_api.calls}
    stats = await run_sync(sessions, user_id)
    assert supply_api.calls["get"] == [[5002]]  # 5001 завершена и с актами, 5003 отменена и с составом
    assert (stats.fetched, stats.new, stats.state_changes, stats.bundles) == (1, 0, 1, 1)
    async with sessions() as db:
        order = (await db.execute(select(SupplyOrder).where(SupplyOrder.ozon_order_id == 5002))).scalar_one()
        events = (await db.execute(select(SupplyOrderEvent.state).where(SupplyOrderEvent.order_id == order.id)
                                   .order_by(SupplyOrderEvent.seen_at))).scalars().all()
        assert events == ["READY_TO_SUPPLY", "IN_TRANSIT"]
        snaps = await db.scalar(select(func.count(func.distinct(SupplyOrderItem.snapshot_at)))
                                .where(SupplyOrderItem.order_id == order.id, SupplyOrderItem.source == BUNDLE))
        assert snaps == 2  # прежний состав остался
        assert [r.quantity for r in await sync_mod.latest_items(db, order.id, BUNDLE) if r.offer_id == TH] == [10]

    # без изменений — ни одной новой записи
    stats = await run_sync(sessions, user_id)
    assert (stats.state_changes, stats.bundles) == (0, 0)


async def test_sync_acts_failure_does_not_break(sessions, client, tokens, ozon, supply_api):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, ozon)
    supply_api.act_status = 500
    stats = await run_sync(sessions, user_id)
    assert (stats.new, stats.acts, stats.acts_failed) == (3, 0, 1)
    async with sessions() as db:
        done = (await db.execute(select(SupplyOrder).where(SupplyOrder.ozon_order_id == 5001))).scalar_one()
        assert done.acts_synced_at is None  # акты подтянем в следующий раз
    supply_api.act_status = 200
    stats = await run_sync(sessions, user_id)
    assert (stats.acts, stats.acts_failed) == (1, 0)


async def test_supply_events_in_feed(sessions, client, tokens, ozon, supply_api):  # noqa: F811
    from tests.test_sync_stocks import auth

    user_id = await api_user(sessions, client, tokens, ozon)
    await run_sync(sessions, user_id)
    data = (await client.get("/api/v1/analytics/events", params={"kind": "stock", "date_from": "2026-09-01"},
                             headers=auth(tokens))).json()
    supply = [e for e in data["items"] if e["key"].startswith("supply:")]
    assert [(e["title"], e["tone"]) for e in supply] == [
        ("Поставка №2000000005002: готова к отгрузке", None), ("Поставка №2000000005003: отменена", "bad"),
        ("Поставка №2000000005001: завершена", "ok")]
    assert supply[2]["detail"] == "Санкт-Петербург и СЗО, 25 шт." and "tab=orders&order=" in supply[2]["link"]


async def test_old_final_orders_without_content(sessions, client, tokens, ozon, supply_api):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, ozon)
    supply_api.orders["orders"][2]["created_date"] = "2025-01-10T11:00:00Z"  # отменённая полтора года назад
    stats = await run_sync(sessions, user_id)
    assert stats.new == 3 and "bnd-5003" not in supply_api.calls["bundle"]
    supply_api.calls = {k: [] for k in supply_api.calls}
    await run_sync(sessions, user_id)
    assert supply_api.calls["get"] == [[5002]] and supply_api.calls["act"] == []  # давнюю и завершённую не перечитываем
