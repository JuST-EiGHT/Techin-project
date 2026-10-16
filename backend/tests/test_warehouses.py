"""Раздел складов (шаг 5.1): сводка по складам, товары склада, CSV, «Заканчиваются», пороги остатков пользователя.

Остатки — синхронизация на записанных ответах Ozon (как в test_sync_stocks); продажи по товару подменяются:
шаг 3.5 проверен отдельно.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.services import products as products_service
from app.services import stocks as stocks_service
from app.services.sync.finance import today_msk
from tests.test_sync_stocks import KRD, SPB, api_user, auth, ozon  # noqa: F401 — фикстура ozon

TH, ACB, RS, CM = "6001-2RS.C3.SRL.NAC.TH", "ACB305523.NAC.JP", "6000-2RS.C3.SRL.NAC.JP", "6000.CM.NAC.JP"
# После синхронизации: TH — Екатеринбург 9, СПб 10 (в пути 4), FBS 12 → 31 шт.; ACB — СПб 5 (в пути 1);
# RS — Краснодар 0 (в пути 20); CM — FBS 1.


async def product_ids(sessions, user_id) -> dict[str, uuid.UUID]:
    async with sessions() as db:
        rows = await db.execute(select(Product.offer_id, Product.id).where(Product.user_id == user_id))
        return dict(rows.all())


def fake_sales(monkeypatch, ids: dict[str, uuid.UUID], avg: dict[str, str], last: dict[str, int], since: int = 120):
    """Продажи в день по артикулу и сколько дней назад была последняя продажа."""
    today = today_msk()

    async def sales(db, uid, days=30):
        return {ids[offer]: Decimal(v) for offer, v in avg.items()}

    async def last_sales(db, uid, offer_id=None):
        return {offer: today - timedelta(days=d) for offer, d in last.items()}

    async def data_range(db, uid):
        return today - timedelta(days=since), today - timedelta(days=1)

    monkeypatch.setattr(stocks_service, "avg_daily_sales", sales)
    monkeypatch.setattr(products_service, "avg_daily_sales", sales)
    monkeypatch.setattr(stocks_service, "last_sales", last_sales)
    monkeypatch.setattr(stocks_service, "data_range", data_range)


@pytest.fixture
async def seller(sessions, client, tokens, ozon, monkeypatch):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, ozon)
    ids = await product_ids(sessions, user_id)
    # TH: 31 шт. по 6,2 в день — 5 дней; RS: кончился, а продаётся; CM: 1 шт. по 0,1 — 10 дней; ACB не продаётся 45 дней
    fake_sales(monkeypatch, ids, {TH: "6.2", ACB: "0", RS: "1", CM: "0.1"}, {TH: 1, ACB: 45, RS: 2})
    return ids


async def test_requires_auth(client):
    for url in ("/api/v1/warehouses/summary", "/api/v1/stocks/running-out", "/api/v1/stocks/export"):
        assert (await client.get(url)).status_code == 401


async def test_summary(client, tokens, seller):
    data = (await client.get("/api/v1/warehouses/summary", headers=auth(tokens))).json()
    assert data["thresholds"] == {"critical": 7, "low": 14, "excess": 60}
    assert (data["sales_known"], data["stocks_known"]) == (True, True)
    assert data["totals"] == {
        "products": 4, "present": 37, "in_transit": 25,
        "counts": {"critical": 2, "low": 1, "normal": 0, "excess": 1, "no_data": 0},
    }
    by_id = {w["ozon_warehouse_id"]: w for w in data["warehouses"]}
    spb = by_id[SPB]
    assert (spb["type"], spb["cluster_name"], spb["skus"], spb["total_present"], spb["total_in_transit"]) == (
        "FBO", "Санкт-Петербург и СЗО", 2, 15, 5,
    )
    assert spb["counts"] == {"critical": 1, "low": 0, "normal": 0, "excess": 1, "no_data": 0}
    assert [(t["product"]["offer_id"], t["present"], t["status"]) for t in spb["top"]] == [
        (TH, 10, "critical"), (ACB, 5, "excess"),
    ]
    assert spb["top"][0]["available"] == 31 and spb["top"][0]["days_of_supply"] == "5.0"  # по товару в целом
    krd = by_id[KRD]
    assert (krd["skus"], krd["total_present"], krd["total_in_transit"], krd["counts"]["critical"]) == (0, 0, 20, 1)
    assert data["warehouses"][-1]["is_active"] is False  # неактивные — в конце

    # требуют внимания: заканчиваются — меньше всего дней запаса первыми; лежат — дольше не продававшиеся первыми
    assert [(r["product"]["offer_id"], r["days_of_supply"], r["status"]) for r in data["running_out"]] == [
        (RS, "0.0", "critical"), (TH, "5.0", "critical"), (CM, "10.0", "low"),
    ]
    assert data["running_out_total"] == 3
    (idle,) = data["lying"]
    assert (idle["product"]["offer_id"], idle["days_without_sales"], idle["available"], idle["warehouses"]) == (
        ACB, 45, 5, 1,
    )
    assert idle["last_sale"] == (today_msk() - timedelta(days=45)).isoformat()


async def test_summary_without_sales_data(sessions, client, tokens, ozon):  # noqa: F811
    await api_user(sessions, client, tokens, ozon)
    data = (await client.get("/api/v1/warehouses/summary", headers=auth(tokens))).json()
    assert data["sales_known"] is False
    assert data["totals"]["counts"]["no_data"] == 4 and data["running_out"] == [] and data["lying"] == []


async def test_summary_empty(client, tokens):
    data = (await client.get("/api/v1/warehouses/summary", headers=auth(tokens))).json()
    assert (data["stocks_known"], data["warehouses"], data["totals"]["products"]) == (False, [], 0)


async def test_warehouse_products(client, tokens, seller):
    whs = {w["ozon_warehouse_id"]: w["id"] for w in (await client.get("/api/v1/warehouses", headers=auth(tokens))).json()}
    url = f"/api/v1/warehouses/{whs[SPB]}/products"
    rows = (await client.get(url, headers=auth(tokens))).json()
    assert [(r["product"]["offer_id"], r["present"], r["in_transit"], r["status"]) for r in rows] == [
        (TH, 10, 4, "critical"), (ACB, 5, 1, "excess"),
    ]
    assert (rows[1]["days_without_sales"], rows[0]["days_without_sales"]) == (45, 1)
    rows = (await client.get(url, params={"status": "excess"}, headers=auth(tokens))).json()
    assert [r["product"]["offer_id"] for r in rows] == [ACB]

    r = await client.get(f"/api/v1/warehouses/{uuid.uuid4()}/products", headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (404, "Склад не найден")
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    t2 = (await client.post("/api/v1/auth/login", json=other)).json()
    assert (await client.get(url, headers=auth(t2))).status_code == 404  # чужой склад


async def test_stocks_csv(client, tokens, seller):
    r = await client.get("/api/v1/stocks/export", headers=auth(tokens))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    lines = text.strip().split("\r\n")
    assert lines[0].startswith("Артикул;Название;SKU;Склад;Тип склада;Кластер;Доступно, шт.")
    assert len(lines) == 1 + 6
    acb = next(line for line in lines if line.startswith(ACB))
    assert ";избыток;" in acb and ";45" in acb

    whs = {w["ozon_warehouse_id"]: w["id"] for w in (await client.get("/api/v1/warehouses", headers=auth(tokens))).json()}
    params = {"warehouse_id": whs[SPB], "status": "critical"}
    r = await client.get("/api/v1/stocks/export", params=params, headers=auth(tokens))
    rows = r.content.decode("utf-8-sig").strip().split("\r\n")
    assert len(rows) == 2 and rows[1].startswith(TH) and ";6,2;5,0;31;критично;" in rows[1]
    assert "%D0%A1%D0%9F%D0%91_%D0%91%D0%A3%D0%93%D0%A0%D0%AB" in r.headers["content-disposition"]  # имя склада


async def test_running_out_widget(client, tokens, seller):
    data = (await client.get("/api/v1/stocks/running-out", params={"limit": 2}, headers=auth(tokens))).json()
    assert (data["critical"], data["low"], data["sales_known"], data["stocks_known"]) == (2, 1, True, True)
    assert [i["product"]["offer_id"] for i in data["items"]] == [RS, TH]


async def test_running_out_hides_archived(sessions, client, tokens, seller):
    async with sessions() as db:
        product = await db.get(Product, seller[RS])
        product.is_archived = True
        await db.commit()
    data = (await client.get("/api/v1/stocks/running-out", headers=auth(tokens))).json()
    assert [i["product"]["offer_id"] for i in data["items"]] == [TH, CM]


async def test_user_thresholds(client, tokens, seller):
    """Пороги — настройки пользователя: статус на складах, в «Заканчиваются» и на странице товаров."""
    r = await client.patch("/api/v1/settings", json={"stock_critical_days": 3, "stock_low_days": 6, "stock_excess_days": 40},
                           headers=auth(tokens))
    assert r.status_code == 200
    data = (await client.get("/api/v1/warehouses/summary", headers=auth(tokens))).json()
    assert data["thresholds"] == {"critical": 3, "low": 6, "excess": 40}
    statuses = {r["product"]["offer_id"]: r["status"] for r in data["running_out"]}
    assert statuses == {RS: "critical", TH: "low"}  # 5 дней — уже «мало», 10 дней CM — норма
    products = (await client.get("/api/v1/products", headers=auth(tokens))).json()
    assert products["counts"]["critical_stock"] == 1 and products["counts"]["low_stock"] == 2
    rows = (await client.get("/api/v1/stocks", params={"status": "normal"}, headers=auth(tokens))).json()
    assert {r["product"]["offer_id"] for r in rows} == {CM}


def test_days_without_sales():
    from app.engine.stocks import days_without_sales

    assert days_without_sales(None, date(2026, 10, 16)) is None
    assert days_without_sales(date(2026, 10, 1), date(2026, 10, 16)) == 15
    assert days_without_sales(date(2026, 10, 16), date(2026, 10, 16)) == 0
