"""Шаг 10.16: «Акции» в «Товарах» — минимальная и целевая цена для участия (калькулятор 4.2) у товаров акции и
кандидатов, «ниже минимальной», «весь остаток»; результаты участия — во время против такого же периода до (дни без данных
— пропуск, сравнение по средним за день с данными), по акции в целом, в истории участия и на странице товара."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.engine import promo_results as engine
from app.models.product import Product
from app.models.promotion import Promotion, PromotionProduct
from app.models.warehouse import ProductStock, Warehouse
from app.services.sync.finance import MSK, today_msk
from tests.test_pnl_api import add_api_day, user_id
from tests.test_pricing import auth, seed, user_id_of

D = Decimal
E = date(2026, 10, 10)


# ── эталоны (BUSINESS_LOGIC §10, «Акции в «Товарах»») ──
def test_windows():
    assert engine.windows(E, E + timedelta(days=6)) == ((E, E + timedelta(days=6)), (E - timedelta(days=7), E - timedelta(days=1)))
    assert engine.windows(E, E - timedelta(days=3)) == ((E, E), (E - timedelta(days=1), E - timedelta(days=1)))  # выход в день входа
    # длинное участие — по первым 90 дням
    assert engine.windows(E, E + timedelta(days=200)) == ((E, E + timedelta(days=89)), (E - timedelta(days=90), E - timedelta(days=1)))


def test_totals_and_compare_skip_days_without_data():
    days = {}
    for i in range(6):  # во время: 6 дней с данными по 2 шт. × 500 ₽, прибыль 300 ₽ в день; седьмой — без данных
        days[E + timedelta(days=i)] = engine.Day(2, D(1000), D(300))
    for i in range(1, 8):  # до: по 1 шт. за 500 ₽, прибыль 100 ₽; за 3 дня до входа данных нет
        if i != 3:
            days[E - timedelta(days=i)] = engine.Day(1, D(500), D(100))
    (d1, d2), (b1, b2) = engine.windows(E, E + timedelta(days=6))
    during, before = engine.totals(days, d1, d2), engine.totals(days, b1, b2)
    assert (during.days, during.days_with_data, during.qty, during.revenue, during.profit) == (7, 6, 12, D(6000), D(1800))
    assert (before.days_with_data, before.qty, before.revenue, before.profit) == (6, 6, D(3000), D(600))
    c = engine.compare(during, before)
    assert (c["qty_change_pct"], c["revenue_change_pct"], c["profit_change"]) == (D("100.0"), D("100.0"), D(1200))

    # «до» с меньшим числом дней с данными — приводится к дням «во время»: 3 дня по 1 шт. → 6 шт. на 6 дней
    short = {k: v for k, v in days.items() if not (b1 <= k <= b1 + timedelta(days=2))}
    before = engine.totals(short, b1, b2)
    assert before.days_with_data == 3
    c = engine.compare(during, before)
    assert (c["base"].qty, c["base"].revenue, c["qty_change_pct"]) == (6, D(3000), D("100.0"))

    # нет себестоимости в день с продажами — прибыли нет; нет данных «до» — сравнения нет
    days[E] = engine.Day(2, D(1000), None)
    assert engine.totals(days, d1, d2).profit is None
    assert engine.compare(engine.totals(days, d1, d2), engine.totals({}, b1, b2))["known"] is False


def test_combine_products():
    a = (engine.Totals(E, E, 1, 1, 4, D(400), D(100)), engine.Totals(E, E, 1, 1, 2, D(200), D(60)))
    b = (engine.Totals(E, E, 1, 1, 1, D(100), None), engine.Totals(E, E, 1, 1, 1, D(100), D(20)))
    none = (engine.Totals(E, E, 1, 0, 0, D(0), None), engine.Totals(E, E, 1, 1, 3, D(300), D(90)))
    total = engine.combine([a, b, none])
    assert (total["products"], total["qty"], total["qty_before"], total["revenue"], total["revenue_before"]) == (
        2, 5, 3, D(500), D(300))
    # прибыль — по товарам, у которых она есть в обоих окнах: 100 против 60
    assert (total["profit"], total["profit_before"], total["profit_change"]) == (D(100), D(60), D(40))
    assert total["qty_change_pct"] == D("66.7")


# ── через API ──
async def test_participation_prices(client, tokens, sessions):
    p = await seed(sessions, await user_id_of(client, tokens))
    uid = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        whole = Promotion(user_id=uid, ozon_action_id=2, title="Скидка на сток", date_to=now + timedelta(days=5),
                          whole_stock=True, synced_at=now)
        db.add(whole)
        w = Warehouse(user_id=uid, ozon_warehouse_id=1, name="Хоругвино", type="FBO", cluster_name="Москва", synced_at=now)
        db.add(w)
        await db.flush()
        # A — участвует по 400 (ниже минимальной), B — кандидат с максимальной ценой 700
        db.add_all([
            PromotionProduct(promotion_id=whole.id, product_id=p["A"].id, action_price=D(400), is_active=True,
                             first_seen_at=now, synced_at=now),
            PromotionProduct(promotion_id=whole.id, product_id=p["C"].id, action_price=D(600), max_action_price=D(700),
                             is_active=False, first_seen_at=now, synced_at=now),
            ProductStock(product_id=p["A"].id, warehouse_id=w.id, snapshot_at=now, present=10),
        ])
        await db.commit()
        whole_id = whole.id
    calc_a = (await client.post("/api/v1/price/calculate", json={"product_id": str(p["A"].id)}, headers=auth(tokens))).json()
    body = (await client.get(f"/api/v1/promotions/{whole_id}/products", headers=auth(tokens))).json()
    rows = {r["product"]["offer_id"]: r for r in body["items"]}
    a, c = rows["ART-A"], rows["ART-C"]
    # минимальная и целевая — те же, что в калькуляторе цены: 429,89 и 642,18 ₽
    assert (a["breakeven_price"], a["target_price"]) == (calc_a["result"]["min_price"], calc_a["result"]["target_price"])
    assert a["below_breakeven"] is True and D(a["unit_profit"]) < 0
    # весь остаток: 10 шт. по цене акции — убыток с каждой
    assert D(a["whole_stock_profit"]) == D(a["unit_profit"]) * 10
    # кандидат — по максимальной цене для участия
    assert c["is_active"] is False and c["below_breakeven"] is False and D(c["unit_profit"]) > 0
    assert c["whole_stock_profit"] is None  # остатка нет


async def test_results_through_api(client, tokens, sessions):
    await client.put("/api/v1/costs/R-1", json={"cost_price": "100", "vat_multiplier": "1"}, headers=auth(tokens))
    uid = await user_id(client, tokens)
    today = today_msk()
    entered = today - timedelta(days=6)
    # до входа: 6 дней по 1 шт. за 500 ₽ (за 2 дня до входа данных нет); во время: по 2 шт.
    for i in range(1, 8):
        if i != 2:
            await add_api_day(sessions, uid, entered - timedelta(days=i), [("Выручка", "500", 1, "R-1")])
    for i in range(6):
        await add_api_day(sessions, uid, entered + timedelta(days=i), [("Выручка", "1000", 2, "R-1")])
    at = datetime.combine(entered, time(12), MSK).astimezone(timezone.utc)
    async with sessions() as db:
        from sqlalchemy import select

        product = (await db.execute(select(Product).where(Product.offer_id == "R-1"))).scalar_one()
        promo = Promotion(user_id=uid, ozon_action_id=7, title="Неделя скидок", date_to=datetime.now(timezone.utc) + timedelta(days=3),
                          synced_at=at)
        db.add(promo)
        await db.flush()
        db.add(PromotionProduct(promotion_id=promo.id, product_id=product.id, action_price=D(450), is_active=True,
                                first_seen_at=at, synced_at=at))
        await db.commit()
        promo_id, product_id = promo.id, product.id

    r = await client.get(f"/api/v1/promotions/{promo_id}/results", headers=auth(tokens))
    assert r.status_code == 200, r.text
    body = r.json()
    item = body["items"][0]["result"]
    # во время: 7 дней (сегодня без данных) → 6 с данными, 12 шт., 6000 ₽, прибыль 6000 − 12 × 100 = 4800
    assert (item["during"]["days"], item["during"]["days_with_data"], item["during"]["qty"]) == (7, 6, 12)
    assert (item["during"]["revenue"], item["during"]["profit"]) == ("6000.00", "4800.00")
    # до: 7 дней, 6 с данными, 6 шт. по 500 → прибыль 6 × 400 = 2400; рост +100 %, прибыль +2400
    assert (item["before"]["days_with_data"], item["before"]["qty"], item["before"]["profit"]) == (6, 6, "2400.00")
    assert (item["qty_change_pct"], item["revenue_change_pct"], item["profit_change"]) == ("100.0", "100.0", "2400.00")
    assert body["totals"]["products"] == 1 and body["totals"]["profit_change"] == "2400.00" and body["note"]

    # в истории участия и на странице товара — тот же результат
    hist = (await client.get("/api/v1/promotions/history", params={"product_id": str(product_id)}, headers=auth(tokens))).json()
    assert hist["items"][0]["result"]["qty_change_pct"] == "100.0"
    stats = (await client.get(f"/api/v1/products/{product_id}/stats", headers=auth(tokens))).json()
    assert stats["promotions"][0]["result"]["profit_change"] == "2400.00"
    assert (await client.get(f"/api/v1/promotions/{product_id}/results", headers=auth(tokens))).status_code == 404
