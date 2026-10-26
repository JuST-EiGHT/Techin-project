"""Шаг 10.14: «Статистика товара» — GET /products/{id}/stats: продажи и юнит-экономика за период (как «Прибыль по
товарам»), цены и скидки с маржой калькулятора 4.2 (зачёркнутая → ваша → с акциями → в акции → можно в акцию),
остатки по кластерам и своим складам, риск дефицита, история себестоимости, цен через сервис и участия в акциях; товар
другого аккаунта или другого магазина того же аккаунта — 404."""
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.models.price_change import APPLIED, MANUAL_SINGLE, PriceChange
from app.models.product import Product, ProductPrice
from app.models.promotion import Promotion, PromotionProduct
from app.models.user import User
from app.models.warehouse import ProductStock, Warehouse
from app.services.product_page import risk, stock_clusters
from app.services.sync.finance import MSK, today_msk
from tests.test_pnl_api import add_api_day, user_id
from tests.test_report_pnl import OWNER, auth, set_profile

D = Decimal


async def seed(client, tokens, sessions):
    await set_profile(client, tokens, OWNER)
    await client.put("/api/v1/costs/A-1", json={"cost_price": "90", "vat_multiplier": "1",
                                                  "effective_from": "2026-01-01"}, headers=auth(tokens))
    await client.put("/api/v1/costs/A-1", json={"cost_price": "100", "vat_multiplier": "1"}, headers=auth(tokens))
    uid = await user_id(client, tokens)
    today = today_msk()
    await add_api_day(sessions, uid, today - timedelta(days=3), [
        ("Выручка", "1000", 2, "A-1"), ("Вознаграждение за продажу", "-150", None, "A-1"), ("Логистика", "-100", None, "A-1"),
        ("Выручка", "300", 1, "B-2"),
    ])
    now = datetime.now(timezone.utc)
    at = lambda days: datetime.combine(today - timedelta(days=days), time(12), MSK).astimezone(timezone.utc)  # noqa: E731
    async with sessions() as db:
        a = (await db.execute(Product.__table__.select().where(Product.offer_id == "A-1"))).mappings().one()
        aid = a["id"]
        db.add_all([
            ProductPrice(product_id=aid, snapshot_at=at(20), price=D(480), commission_pct=D(15)),
            ProductPrice(product_id=aid, snapshot_at=now, price=D(500), old_price=D(700), marketing_price=D(470),
                         commission_pct=D(15)),
            PriceChange(user_id=uid, product_id=aid, offer_id="A-1", created_at=at(5), change_type=MANUAL_SINGLE,
                        price_before=D(480), price_after=D(500), status=APPLIED, is_applied=True),
        ])
        going = Promotion(user_id=uid, ozon_action_id=11, title="Скидки недели", date_to=now + timedelta(days=5),
                          is_participating=True, synced_at=now)
        open_ = Promotion(user_id=uid, ozon_action_id=12, title="Распродажа", date_to=now + timedelta(days=9),
                          synced_at=now)
        db.add_all([going, open_])
        await db.flush()
        db.add_all([
            PromotionProduct(promotion_id=going.id, product_id=aid, action_price=D(400), price=D(500), is_active=True,
                             first_seen_at=at(4), synced_at=now),
            PromotionProduct(promotion_id=open_.id, product_id=aid, action_price=D(420), max_action_price=D(450),
                             price=D(500), is_active=False, first_seen_at=now, synced_at=now),
        ])
        msk = [Warehouse(user_id=uid, ozon_warehouse_id=n, name=name, type="FBO", cluster_id=1, cluster_name="Москва",
                         synced_at=now) for n, name in ((1, "Хоругвино"), (2, "Ногинск"))]
        own = Warehouse(user_id=uid, ozon_warehouse_id=3, name="Свой склад", type="FBS", synced_at=now)
        db.add_all([*msk, own])
        await db.flush()
        db.add_all([
            ProductStock(product_id=aid, warehouse_id=msk[0].id, snapshot_at=now, present=5, in_transit=2),
            ProductStock(product_id=aid, warehouse_id=msk[1].id, snapshot_at=now, present=3, in_transit=0),
            ProductStock(product_id=aid, warehouse_id=own.id, snapshot_at=now, present=4, reserved=1),
        ])
        await db.commit()
    return uid, aid


async def stats(client, tokens, pid, headers=None, **params):
    r = await client.get(f"/api/v1/products/{pid}/stats", params=params, headers=headers or auth(tokens))
    return r


async def test_product_stats(client, tokens, sessions):
    uid, aid = await seed(client, tokens, sessions)
    r = await stats(client, tokens, aid)
    assert r.status_code == 200, r.text
    body = r.json()
    item = body["detail"]["item"]
    assert item["offer_id"] == "A-1" and item["sold_qty"] == 2 and item["period_profit"] == "570.00"

    # период: как «Прибыль по товарам» — себестоимость на день продажи (90, новая 100 — с сегодня):
    # 1000 − 150 − 100 − 2 × 90 = 570, на 1 шт. — 285
    assert body["period"]["data_source"] == "api" and body["period"]["days_total"] == 30
    pnl = body["pnl"]
    assert (pnl["qty"], pnl["profit"], pnl["per_unit"]["profit"]) == (2, "570.00", "285.00")

    # цены: зачёркнутая (без маржи) → ваша → с вашими акциями → в акции → можно в акцию
    levels = body["prices"]["levels"]
    assert [lv["kind"] for lv in levels] == ["old", "price", "marketing", "promo", "candidate"]
    old, price, marketing, promo, candidate = levels
    assert old["margin"] is None and old["price"] == "700.00"
    assert price["margin"] == item["margin"] and price["profit_change"] is None
    assert promo["title"] == "Скидки недели" and promo["discount_pct"] == "20.0"
    assert D(promo["margin"]) == D(item["promotions"][0]["margin_in_promo"])
    assert D(promo["profit_change"]) == D(promo["unit_profit"]) - D(price["unit_profit"]) < 0
    assert candidate["title"] == "Распродажа" and candidate["price"] == "450.00"  # максимальная цена для участия
    assert D(marketing["discount_pct"]) == D("6.0") and body["prices"]["missing"] == []

    # остатки: кластер «Москва» — два склада Ozon вместе, свой склад — отдельно
    clusters = {c["name"]: c for c in body["clusters"]}
    assert (clusters["Москва"]["present"], clusters["Москва"]["in_transit"]) == (8, 2)
    assert sorted(clusters["Москва"]["warehouses"]) == ["Ногинск", "Хоругвино"]
    assert (clusters["Свой склад"]["type"], clusters["Свой склад"]["present"], clusters["Свой склад"]["reserved"]) == (
        "FBS", 4, 1)
    assert body["supply_known"] is False and body["risk"]["level"] in ("ok", "low", "critical", "excess", "unknown")

    # история: себестоимость — новые первыми, цена через сервис, участие в акции
    assert [c["cost_price"] for c in body["cost_history"]] == ["100.00", "90.00"]
    assert body["price_changes_total"] == 1 and body["price_changes"][0]["price_after"] == "500.00"
    assert body["promotions_total"] == 1 and body["promotions"][0]["promotion"]["title"] == "Скидки недели"

    # период без начислений — нет данных, а не 0
    empty = (await stats(client, tokens, aid, date_from="2020-01-01", date_to="2020-01-31")).json()
    assert empty["pnl"] is None and empty["period"]["data_source"] == "none" and empty["detail"]["item"]["sold_qty"] is None
    assert (await stats(client, tokens, aid, date_from="2026-02-01", date_to="2026-01-01")).status_code == 400


async def test_foreign_product_is_404(client, tokens, sessions):
    uid, aid = await seed(client, tokens, sessions)
    assert (await stats(client, tokens, uuid.uuid4())).status_code == 404
    # другой аккаунт
    other = await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "otherpass123"})
    assert other.status_code in (200, 201), other.text
    login = await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "otherpass123"})
    r = await stats(client, tokens, aid, headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert r.status_code == 404 and r.json()["detail"] == "Товар не найден"
    # другой магазин того же аккаунта: товар первого — не найден
    async with sessions() as db:
        store = User(owner_id=uid, store_name="Второй")
        db.add(store)
        await db.commit()
        store_id = str(store.id)
    r = await stats(client, tokens, aid, headers={**auth(tokens), "X-Store-Id": store_id})
    assert r.status_code == 404


def test_clusters_and_risk():
    stocks = [
        {"warehouse": "Хоругвино", "type": "FBO", "cluster_name": "Москва", "present": 2, "in_transit": None,
         "reserved": None},
        {"warehouse": "Склад у дома", "type": "FBS", "cluster_name": None, "present": 10, "in_transit": None,
         "reserved": 0},
    ]
    supply_item = lambda kind, name, urgency, days, late=False: {  # noqa: E731
        "kind": kind, "urgency": urgency, "destination": {"name": name, "warehouse_type": None},
        "avg_daily": D(3), "rate": D(3), "days_left": days, "to_order": 40, "target_stock": 42, "arrives_late": late,
        "sold_30": 90,
    }
    items = [supply_item("supply", "Москва", "critical", D("0.7"), True), supply_item("supply", "Казань", "urgent", None)]
    clusters = stock_clusters(stocks, items)
    # срочные — первыми; Казань — товара там нет, но покупают
    assert [c["name"] for c in clusters] == ["Москва", "Казань", "Склад у дома"]
    assert clusters[1]["present"] == 0 and clusters[0]["supply"]["to_order"] == 40
    r = risk({"stock_status": "normal", "days_of_supply": D(30), "stock": 12}, items)
    assert r["level"] == "critical"
    assert r["text"] == "«Москва»: запаса на 0,7 дн. — кончится раньше, чем доедет поставка, отправить 40 шт.; ещё мест с риском: 1"
    assert risk({"stock_status": "low", "days_of_supply": D("9.5"), "stock": 5}, [])["text"] == (
        "Запаса на всех складах — на 9,5 дн. продаж")
    assert risk({"stock_status": None, "days_of_supply": None, "stock": 0}, [])["level"] == "unknown"
