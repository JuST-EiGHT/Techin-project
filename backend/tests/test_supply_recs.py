"""Шаг 10.18a: данные Ozon по кластерам для поставок — показатели товара в кластере из остатков (снимок при изменении) и
рекомендованная поставка Ozon (`/v1/analytics/local-sale/items-clusters/info`): запрос, снимки, нулевая запись, сбой
бета-метода не мешает заявкам на поставку."""
import copy
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import settings
from app.integrations.ozon.client import OzonClient
from app.models.cluster_metrics import ClusterStockMetric, OzonSupplyRec, OzonSupplyRecSync
from app.models.sync_run import SUCCESS
from app.services.sync import runner
from app.services.sync import supply_recs as recs_mod
from app.services.sync.finance import today_msk
from app.services.sync.stocks import latest_cluster_metrics
from tests.test_supply_orders import FakeSupply
from tests.test_sync_stocks import API_KEY, api_user, sync
from tests.test_sync_stocks import ozon as stocks_ozon  # noqa: F401 — фикстура остатков

FIX = Path(__file__).parent / "fixtures" / "ozon" / "supply"


class FakeLocalSale:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.data = json.loads((FIX / "local_sale_items_clusters.json").read_text(encoding="utf-8"))
        self.calls: list[dict] = []
        self.status = 200
        mock.post(recs_mod.LOCAL_SALE_PATH).mock(side_effect=self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"code": 13, "message": "beta is down"})
        page = self.data["items"][body["offset"]:body["offset"] + body["limit"]]
        return httpx.Response(200, json={"items": page, "total": len(self.data["items"])})


@pytest.fixture
def local_sale():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeLocalSale(mock)


async def sync_recs(sessions, user_id, names=None) -> dict:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await recs_mod.sync_supply_recs(db, user_id, client, names or {777: "Новосибирск"})


async def latest(sessions, user_id) -> dict:
    async with sessions() as db:
        return {k: (r.recommended, r.attention_level, r.cluster_name, r.product_id is not None)
                for k, r in (await recs_mod.latest_recs(db, user_id)).items()}


# ── рекомендации Ozon ──
async def test_sync_recs_request_and_snapshots(sessions, client, tokens, stocks_ozon, local_sale, monkeypatch):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    monkeypatch.setattr(recs_mod, "PAGE", 3)  # две страницы по offset
    stats = await sync_recs(sessions, user_id)
    assert stats == {"ozon_recs": 4, "ozon_recs_changed": 4, "ozon_recs_positive": 3}
    today = today_msk()
    first = local_sale.calls[0]
    assert first["filter"] == {"delivery_schema": "ALL", "supply_period": "ONE_WEEK",
                               "period": {"from": (today - timedelta(days=28)).isoformat(),
                                          "to": (today - timedelta(days=1)).isoformat()}}
    assert [(c["offset"], c["limit"]) for c in local_sale.calls] == [(0, 3), (3, 3)]
    assert await latest(sessions, user_id) == {
        (900001001, 154): (30, "MEDIUM", None, True),
        (900001002, 777): (3, "HIGH", "Новосибирск", True),
        (900001001, 2): (0, "LOW", None, True),
        (900009999, 154): (2, "LOW", None, False),  # товара нет в каталоге — рекомендация сохраняется по SKU
    }
    async with sessions() as db:
        nsk = (await recs_mod.latest_recs(db, user_id))[(900001002, 777)]
        assert nsk.reasons == [{"reason": "NO_SUPPLIES_TO_CLUSTER", "quantity": 5, "amount": "150.00"}]
        state = await db.get(OzonSupplyRecSync, user_id)
        assert (state.ok, state.items, state.error, state.synced_at is not None) == (True, 4, None, True)

    # без изменений — новых записей нет; изменилась рекомендация — новая запись; пропала — нулевая
    assert (await sync_recs(sessions, user_id))["ozon_recs_changed"] == 0
    local_sale.data["items"][0]["metrics"]["recommended_supply"] = 45
    del local_sale.data["items"][1]
    assert (await sync_recs(sessions, user_id))["ozon_recs_changed"] == 2
    now = await latest(sessions, user_id)
    assert now[(900001001, 154)][0] == 45 and now[(900001002, 777)][0] == 0
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OzonSupplyRec)) == 6  # прежние записи остаются


async def test_supplies_job_survives_recs_failure(sessions, client, tokens, stocks_ozon, local_sale):  # noqa: F811
    """Бета-метод не ответил — заявки на поставку загружены, рекомендации — «не получилось», с текстом ошибки."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)
        local_sale.status = 500
        mock.post(recs_mod.LOCAL_SALE_PATH).mock(side_effect=local_sale._handle)
        run = await runner.run_sync("supplies", user_id, sessions)
    assert run.status == SUCCESS and run.stats["orders"] == 3 and "ozon_recs_failed" in run.stats
    async with sessions() as db:
        state = await db.get(OzonSupplyRecSync, user_id)
        assert state.ok is False and state.error and state.synced_at is None

    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)
        local_sale.status = 200
        mock.post(recs_mod.LOCAL_SALE_PATH).mock(side_effect=local_sale._handle)
        run = await runner.run_sync("supplies", user_id, sessions)
    assert run.stats["ozon_recs"] == 4 and run.stats["ozon_recs_positive"] == 3
    async with sessions() as db:
        assert (await db.get(OzonSupplyRecSync, user_id)).ok is True
        # названия кластеров — из /v1/cluster/list; Новосибирска в нём нет — без названия
        names = {k: r.cluster_name for k, r in (await recs_mod.latest_recs(db, user_id)).items()}
        assert (names[(900001001, 154)], names[(900001002, 777)]) == ("Екатеринбург", None)


# ── показатели Ozon по кластеру из остатков ──
async def test_cluster_metrics_from_stock_sync(sessions, client, tokens, stocks_ozon):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    async with sessions() as db:
        metrics = await latest_cluster_metrics(db, user_id)
    by = {(m.cluster_id, m.macrolocal_cluster_id): (m.ads, m.idc, m.turnover_grade) for m in metrics.values()
          if m.product_id is not None}
    assert by[(154, 154)] == (Decimal("2.000"), Decimal("4.5"), "DEFICIT")
    assert len(metrics) == 4
    assert (await sync(sessions, user_id)).cluster_metrics_changed == 0  # не изменилось — новых снимков нет
    for row in stocks_ozon.fbo["items"]:
        if row["cluster_id"] == 154:
            row["ads_cluster"] = 2.5
    assert (await sync(sessions, user_id)).cluster_metrics_changed == 1
    async with sessions() as db:
        latest_ekb = [m for m in (await latest_cluster_metrics(db, user_id)).values() if m.cluster_id == 154]
        assert [m.ads for m in latest_ekb] == [Decimal("2.500")]
        assert await db.scalar(select(func.count()).select_from(ClusterStockMetric)) == 5  # история — остаётся
        from app.models.warehouse import Warehouse

        macros = {w.cluster_id: w.macrolocal_cluster_id for w in (await db.execute(select(Warehouse))).scalars()
                  if w.cluster_id}
    assert macros == {154: 154, 2: 2, 12: 999}
