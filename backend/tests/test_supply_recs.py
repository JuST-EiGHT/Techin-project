"""Шаг 10.18a: данные Ozon по кластерам для поставок — показатели товара в кластере из остатков (снимок при изменении) и
рекомендованная поставка Ozon (`/v1/analytics/local-sale/items-clusters/info`): запрос, снимки, нулевая запись, сбой
бета-метода не мешает заявкам на поставку.

Правка 16.11: фильтр `skus` у метода обязателен (без него Ozon отвечает 400 — так и было на реальном ключе), заглушка
отвечает так же; срок рекомендации (`supply_period`) — настройка «Рекомендация Ozon — на сколько дней» (по умолчанию 28)."""
import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.config import settings
from app.integrations.ozon.client import OzonClient
from app.models.cluster_metrics import ClusterStockMetric, OzonSupplyRec, OzonSupplyRecSync
from app.models.product import Product
from app.models.sync_run import SUCCESS
from app.services.sync import runner
from app.services.sync import supply_recs as recs_mod
from app.services.sync.finance import today_msk
from app.services.sync.stocks import latest_cluster_metrics
from tests.test_settings import auth
from tests.test_supply_orders import FakeSupply
from tests.test_sync_stocks import API_KEY, api_user, sync
from tests.test_sync_stocks import ozon as stocks_ozon  # noqa: F401 — фикстура остатков

FIX = Path(__file__).parent / "fixtures" / "ozon" / "supply"


class FakeLocalSale:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.data = json.loads((FIX / "local_sale_items_clusters.json").read_text(encoding="utf-8"))
        self.calls: list[dict] = []
        self.cluster_calls: list[dict] = []
        self.status = 200
        self.items_zero = False  # как на реальном ключе 18.11: «товар → кластеры» — строки есть, рекомендации везде 0
        self.clusters_status = 200
        self.mount(mock)

    def mount(self, mock: respx.MockRouter) -> None:
        mock.post(recs_mod.LOCAL_SALE_PATH).mock(side_effect=self._handle)
        mock.post(recs_mod.CLUSTERS_PATH).mock(side_effect=self._handle_clusters)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"code": 13, "message": "beta is down"})
        skus = body["filter"].get("skus")
        if not skus:  # как Ozon: фильтр skus обязателен
            return httpx.Response(400, json={"code": 3, "message": "invalid request: filter.skus: value is required"})
        found = [i for i in self.data["items"] if str(i["sku"]) in skus]
        if self.items_zero:
            found = [{**i, "metrics": {**i["metrics"], "recommended_supply": 0}} for i in found]
        page = found[body["offset"]:body["offset"] + body["limit"]]
        return httpx.Response(200, json={"items": page, "total": len(found)})

    def _handle_clusters(self, request: httpx.Request) -> httpx.Response:
        """«Кластер → товары»: кластеры доставки обязательны, SKU — по желанию; строка — {cluster_to_id, item, metrics}."""
        body = json.loads(request.content)
        self.cluster_calls.append(body)
        if self.clusters_status != 200:
            return httpx.Response(self.clusters_status, json={"code": 3, "message": "второй отказ"})
        clusters = body["filter"].get("macrolocal_cluster_to_ids")
        if not clusters:
            return httpx.Response(400, json={"code": 3, "message": "invalid request: macrolocal_cluster_to_ids is required"})
        skus = body["filter"].get("skus")
        found = [{"cluster_to_id": i["macrolocal_cluster_to_id"], "item": {"sku": i["sku"], "offer_id": f"o-{i['sku']}"},
                  "metrics": i["metrics"]} for i in self.data["items"]
                 if str(i["macrolocal_cluster_to_id"]) in clusters and (not skus or str(i["sku"]) in skus)]
        page = found[body["offset"]:body["offset"] + body["limit"]]
        return httpx.Response(200, json={"items": page, "total": len(found)})


@pytest.fixture
def local_sale():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield FakeLocalSale(mock)


async def sync_recs(sessions, user_id, names=None) -> dict:
    async with sessions() as db, OzonClient("1234567", API_KEY) as client:
        return await recs_mod.sync_supply_recs(db, user_id, client, names or {777: "Новосибирск"})


async def catalog_skus(sessions, user_id) -> list[str]:
    async with sessions() as db:
        return sorted(str(p.ozon_sku) for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()
                      if p.ozon_sku and not p.is_archived)


async def latest(sessions, user_id) -> dict:
    async with sessions() as db:
        return {k: (r.recommended, r.attention_level, r.cluster_name, r.product_id is not None)
                for k, r in (await recs_mod.latest_recs(db, user_id)).items()}


# ── рекомендации Ozon ──
async def test_sync_recs_request_and_snapshots(sessions, client, tokens, stocks_ozon, local_sale, monkeypatch):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    monkeypatch.setattr(recs_mod, "PAGE", 2)  # две страницы по offset
    skus = await catalog_skus(sessions, user_id)
    assert "900001001" in skus and "900009999" not in skus  # SKU не из каталога Ozon не спрашиваем
    stats = await sync_recs(sessions, user_id)
    assert stats == {"ozon_recs": 3, "ozon_recs_changed": 3, "ozon_recs_positive": 2, "ozon_recs_source": "items"}
    today = today_msk()
    first = local_sale.calls[0]
    # skus — обязательно, строками; срок рекомендации — 28 дней (раз в 4 недели), как «на 28 дней» в кабинете
    # схема — FBO: рекомендация поставки у Ozon — для FBO (с ALL реальный Ozon прислал нули, правка 18.11)
    assert first["filter"] == {"delivery_schema": "FBO", "supply_period": "FOUR_WEEKS", "skus": skus,
                               "period": {"from": (today - timedelta(days=28)).isoformat(),
                                          "to": (today - timedelta(days=1)).isoformat()}}
    assert [(c["offset"], c["limit"]) for c in local_sale.calls] == [(0, 2), (2, 2)]
    assert await latest(sessions, user_id) == {
        (900001001, 154): (30, "MEDIUM", None, True),
        (900001002, 777): (3, "HIGH", "Новосибирск", True),
        (900001001, 2): (0, "LOW", None, True),
    }
    async with sessions() as db:
        nsk = (await recs_mod.latest_recs(db, user_id))[(900001002, 777)]
        assert nsk.reasons == [{"reason": "NO_SUPPLIES_TO_CLUSTER", "quantity": 5, "amount": "150.00"}]
        assert nsk.period_days == 28
        state = await db.get(OzonSupplyRecSync, user_id)
        assert (state.ok, state.items, state.error, state.synced_at is not None, state.period_days) == (
            True, 3, None, True, 28)

    # без изменений — новых записей нет; изменилась рекомендация — новая запись; пропала — нулевая
    assert (await sync_recs(sessions, user_id))["ozon_recs_changed"] == 0
    local_sale.data["items"][0]["metrics"]["recommended_supply"] = 45
    del local_sale.data["items"][1]
    assert (await sync_recs(sessions, user_id))["ozon_recs_changed"] == 2
    now = await latest(sessions, user_id)
    assert now[(900001001, 154)][0] == 45 and now[(900001002, 777)][0] == 0
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OzonSupplyRec)) == 5  # прежние записи остаются


async def test_recs_period_setting_and_sku_batches(sessions, client, tokens, stocks_ozon, local_sale, monkeypatch):  # noqa: F811
    """Срок рекомендации — настройка: 7 дней → ONE_WEEK, и это новая запись даже при тех же цифрах (Ozon считал на
    другой срок); товары — пачками по SKU_BATCH."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    await sync_recs(sessions, user_id)
    r = await client.patch("/api/v1/settings", headers=auth(tokens), json={"supply_ozon_days": 7})
    assert r.status_code == 200 and r.json()["supply_ozon_days"] == 7
    bad = await client.patch("/api/v1/settings", headers=auth(tokens), json={"supply_ozon_days": 10})
    assert bad.status_code == 400 and "Рекомендация Ozon" in bad.json()["detail"]  # только 7 / 14 / 28 / 56
    local_sale.calls.clear()
    monkeypatch.setattr(recs_mod, "SKU_BATCH", 1)
    stats = await sync_recs(sessions, user_id)
    skus = await catalog_skus(sessions, user_id)
    assert [c["filter"]["skus"] for c in local_sale.calls] == [[s] for s in skus]
    assert {c["filter"]["supply_period"] for c in local_sale.calls} == {"ONE_WEEK"}
    assert stats["ozon_recs_changed"] == 3
    async with sessions() as db:
        assert {r.period_days for r in (await recs_mod.latest_recs(db, user_id)).values()} == {7}
        assert (await db.get(OzonSupplyRecSync, user_id)).period_days == 7


async def test_supplies_job_survives_recs_failure(sessions, client, tokens, stocks_ozon, local_sale):  # noqa: F811
    """Бета-метод отклонил запрос — заявки на поставку загружены, рекомендации — «не получилось», с методом и текстом Ozon."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)
        local_sale.status = 400  # как было на реальном ключе: Ozon отклонил запрос
        local_sale.mount(mock)
        run = await runner.run_sync("supplies", user_id, sessions)
    assert run.status == SUCCESS and run.stats["orders"] == 3 and "ozon_recs_failed" in run.stats
    async with sessions() as db:
        state = await db.get(OzonSupplyRecSync, user_id)
        assert state.ok is False and state.synced_at is None
        # причина — с методом и текстом Ozon, а не голое «HTTP 500»
        assert recs_mod.LOCAL_SALE_PATH in state.error and "beta is down" in state.error

    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)
        local_sale.status = 200
        local_sale.mount(mock)
        run = await runner.run_sync("supplies", user_id, sessions)
    assert run.stats["ozon_recs"] == 3 and run.stats["ozon_recs_positive"] == 2
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


async def test_sync_falls_back_to_clusters_items(sessions, client, tokens, stocks_ozon, local_sale):  # noqa: F811
    """Правка 19.11, как на реальном ключе: «товар → кластеры» прислал строки, но везде 0 — спрашиваем «кластер → товары»
    по кластерам Ozon и складов магазина и берём его рекомендации; только товары магазина."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    local_sale.items_zero = True
    stats = await sync_recs(sessions, user_id)
    assert (stats["ozon_recs_source"], stats["ozon_recs_positive"]) == ("clusters", 2)
    flt = local_sale.cluster_calls[0]["filter"]
    assert flt["macrolocal_cluster_to_ids"] == ["2", "154", "777", "999"]  # /v1/cluster/list + кластеры складов
    assert flt["delivery_schema"] == "FBO" and flt["supply_period"] == "FOUR_WEEKS" and "skus" not in flt
    assert (await latest(sessions, user_id))[(900001001, 154)][0] == 30
    # «товар → кластеры» снова даёт цифры — второй метод не нужен
    local_sale.items_zero, local_sale.cluster_calls = False, []
    assert (await sync_recs(sessions, user_id))["ozon_recs_source"] == "items" and local_sale.cluster_calls == []


async def test_sync_zero_everywhere_stays_zero(sessions, client, tokens, stocks_ozon, local_sale):  # noqa: F811
    """Оба метода — нули: сохраняем нули «товар → кластеры» (нет данных ≠ рекомендация), загрузка не падает."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    local_sale.items_zero = True
    local_sale.data["items"] = [{**i, "metrics": {**i["metrics"], "recommended_supply": 0}} for i in local_sale.data["items"]]
    stats = await sync_recs(sessions, user_id)
    assert (stats["ozon_recs_source"], stats["ozon_recs"], stats["ozon_recs_positive"]) == ("items", 3, 0)
    # что дал второй метод — в статистике загрузки (на сервере её видно запросом к sync_runs, журнал — нет)
    assert {k: v for k, v in stats.items() if k.startswith("ozon_recs_clusters")} == {
        "ozon_recs_clusters_asked": 4, "ozon_recs_clusters_rows": 4, "ozon_recs_clusters_ours": 3,
        "ozon_recs_clusters_positive": 0}

    local_sale.clusters_status = 400  # второй метод отказал — загрузка не падает, причина — в статистике
    stats = await sync_recs(sessions, user_id)
    assert stats["ozon_recs_source"] == "items" and "второй отказ" in stats["ozon_recs_clusters_error"]


async def test_sync_buyer_demand_without_recommendation(sessions, client, tokens, stocks_ozon, local_sale):  # noqa: F811
    """Правка 21.11: на реальном ключе в ответе нет `recommended_supply` — сохраняем спрос покупателей кластера
    (`local_data`: купили всего и из них со складов кластера), по нему считаем рекомендацию по формуле Ozon; изменился
    спрос — новая запись, пропала строка со спросом — нулевая."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    for i in local_sale.data["items"]:
        i["metrics"].pop("recommended_supply", None)
    stats = await sync_recs(sessions, user_id)
    assert (stats["ozon_recs"], stats["ozon_recs_positive"], stats["ozon_recs_changed"]) == (3, 0, 3)

    async def demand() -> dict:
        async with sessions() as db:
            return {k: (r.recommended, r.demand_qty, r.local_qty) for k, r in (await recs_mod.latest_recs(db, user_id)).items()}

    assert await demand() == {(900001001, 154): (0, 10, 6), (900001002, 777): (0, 5, 0), (900001001, 2): (0, 3, 3)}
    assert (await sync_recs(sessions, user_id))["ozon_recs_changed"] == 0  # то же — новых записей нет
    local_sale.data["items"][0]["metrics"]["local_data"]["total_quantity"] = 12
    del local_sale.data["items"][1]
    assert (await sync_recs(sessions, user_id))["ozon_recs_changed"] == 2
    now = await demand()
    assert now[(900001001, 154)] == (0, 12, 6) and now[(900001002, 777)] == (0, 0, 0)
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OzonSupplyRec)) == 5  # прежние записи остаются

    # в отчёте поставок: ACB в Новосибирске — продаж Ozon в кластере нет, спрос покупателей был 5 шт. за 28 дней —
    # Ozon советует ⌈5 / 28 × 28 × 1,2⌉ = 6 по формуле (вернули строку — снова есть спрос)
    local_sale.data = json.loads((FIX / "local_sale_items_clusters.json").read_text(encoding="utf-8"))
    for i in local_sale.data["items"]:
        i["metrics"].pop("recommended_supply", None)
    await sync_recs(sessions, user_id)
    data = (await client.get("/api/v1/supply/recommendations", headers=auth(tokens))).json()
    nsk = next(i for i in data["items"] if i["destination"]["name"] == "Новосибирск")
    assert (nsk["product"]["ozon_sku"], nsk["ozon_recommended"], nsk["ozon_estimated"], nsk["own_need"]) == (
        900001002, 6, True, None)
    assert nsk["to_order"] == 6 and data["ozon_data"]["formula"] is True and data["ozon_data"]["recs_positive"] == 0


async def test_admin_supply_recs_check(sessions, client, tokens, stocks_ozon, local_sale, monkeypatch):  # noqa: F811
    """`admin.sh supply-recs-check`: одни и те же товары разными запросами — что ответил Ozon, поля и первая строка
    ответа; `--offer` — только эти товары; в базу ничего не пишет."""
    from tests.conftest import USER
    from tests.test_deploy import run_admin

    monkeypatch.setattr(recs_mod, "PROBE_PAUSE", 0)
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    local_sale.items_zero = True
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)  # /v1/cluster/list — названия кластеров
        local_sale.mount(mock)
        code, text = await run_admin(sessions, "supply-recs-check", USER["email"])
    assert code == 0, text
    assert [c["filter"]["delivery_schema"] for c in local_sale.calls] == ["FBO", "ALL", "FBO", "FBO"]
    assert "supply_period" not in local_sale.calls[3]["filter"]  # вариант «без срока поставки»
    assert local_sale.calls[2]["filter"]["period"]["to"] == today_msk().isoformat()  # «период до сегодня»
    assert "товар → кластеры, FBO, 28 дн.: строк 3, с рекомендацией 0" in text
    # спрашиваем кластеры из /v1/cluster/list и складов магазина; Новосибирска (777) там нет — его строки нет
    assert "кластер → товары, FBO, 28 дн.: строк 2, с рекомендацией 1 (всего 30 шт.)" in text
    assert "→ кластер 154 Екатеринбург: 30 шт." in text
    assert "поля metrics: attention_level, impact_share, local_data" in text
    assert 'первая строка: {"cluster_to_id": 154, "item": {' in text  # как прислал Ozon, а не переложенная
    # 21.11: сверка с кабинетом — что сервис показывает в «Поставках» (загрузки ещё не было — по остаткам и продажам Ozon)
    assert "артикул | кластер | продажи в день | остаток | в пути | Ozon советует | везём" in text
    assert "6001-2RS.C3.SRL.NAC.TH | Екатеринбург | 2 | 9 | 0 | 59 по формуле | 95" in text

    # у аккаунта два магазина с ключами — проверяются оба (20.11: на втором команда падала — переменная с названиями
    # магазинов затиралась названиями кластеров)
    from app.models.api_credential import ApiCredential
    from app.models.user import User
    from app.services.encryption import encrypt

    async with sessions() as db:
        second = User(id=uuid.uuid4(), owner_id=user_id, store_name="Второй магазин")
        db.add(second)
        await db.flush()
        db.add(ApiCredential(user_id=second.id, label="Второй", client_id="7654321", api_key_encrypted=encrypt("k" * 36),
                             expires_at=datetime.now(UTC) + timedelta(days=30)))
        db.add(Product(user_id=second.id, ozon_product_id=77, offer_id="ВТОРОЙ-1", ozon_sku=900001001,
                       synced_at=datetime.now(UTC)))
        await db.commit()
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)
        local_sale.mount(mock)
        code, text = await run_admin(sessions, "supply-recs-check", USER["email"])
    assert code == 0, text
    assert "«Основной магазин»: товаров в запросе" in text and "«Второй магазин»: товаров в запросе 1" in text

    local_sale.calls.clear()
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        FakeSupply(mock)
        local_sale.mount(mock)
        code, text = await run_admin(sessions, "supply-recs-check", USER["email"], "--offer", "НЕТ-ТАКОГО")
    assert code == 0 and "нет в каталоге (или без SKU): нет-такого" in text and "нет товаров для запроса" in text
    assert "«Второй магазин»: нет в каталоге" in text
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OzonSupplyRec)) == 0  # только чтение
        assert await db.get(OzonSupplyRecSync, user_id) is None

