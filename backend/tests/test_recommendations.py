"""Шаг 10.6: «Рекомендуемые действия» — правила (акция в минус, цена ниже минимальной, поставка, себестоимость, вывоз),
достаточность данных, тексты с цифрами, порядок; API: по магазину и товару, «Скрыть» на N дней, «Вернуть скрытые»."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.engine import recommendations as rec
from app.engine.anomaly_detector import CostGap
from app.models.recommendation import RecommendationHide
from tests.test_products import seed, user_id_of

D = Decimal
TODAY = date(2026, 10, 23)


def ref(offer: str, name: str | None = None) -> rec.ProductRef:
    return rec.ProductRef(uuid.uuid4(), offer, name)


def product(offer: str = "6201-2RS", **kw) -> rec.ProductData:
    kw.setdefault("price", D("1000"))
    kw.setdefault("has_cost", True)
    kw.setdefault("avg_daily", D("2"))
    return rec.ProductData(ref(offer, "Подшипник"), **kw)


def inputs(*products: rec.ProductData, data_days: int = 30, **kw) -> rec.Inputs:
    return rec.Inputs(today=TODAY, data_days=data_days, products={p.product.id: p for p in products}, **kw)


# ── правила ──
def test_price_below_min_why_with_numbers():
    p = product("A-1", price=D("900"), min_price=D("1050"), target_price=D("1200"), margin=D("-4.5"),
                unit_profit=D("-40.5"))
    [r] = rec.recommend(inputs(p))
    assert (r.kind, r.key, r.title) == (rec.PRICE, f"price:{p.product.id}", "Проверить цену A-1")
    assert r.why == ("Цена 900 ₽ ниже минимальной 1 050 ₽ — маржа −4,5 %, с каждой продажи минус 40,50 ₽; продаётся 2 "
                     "шт. в день. Без убытка — от 1 050 ₽ (+16,7 %), целевая — 1 200 ₽.")
    assert r.money == D("2430.00") and r.money_label == "убыток за 30 дней"  # 40,5 ₽ × 2 шт. × 30 дней
    assert r.link == f"/dashboard/products/prices?product={p.product.id}" and "калькулятор минимальной цены" in r.basis
    assert rec.recommend(inputs(product(price=D("1100"), min_price=D("1050")))) == []  # цена не ниже — нет


def test_promo_loss_until_promo_ends():
    promo = rec.PromoData(uuid.uuid4(), "Распродажа", D("800"), D("-6"), TODAY + timedelta(days=4), auto=True)
    p = product("B-2", avg_daily=D("1"), promo=promo)
    [r] = rec.recommend(inputs(p))
    assert r.title == "Выйти из акции «Распродажа» или поднять цену B-2"
    assert r.why == ("Ozon добавил товар сам. В акции по цене 800 ₽ маржа −6 % — с каждой продажи минус около 48 ₽; "
                     "продаётся 1 шт. в день, акция идёт ещё 5 дней.")
    assert (r.money, r.money_label) == (D("240.00"), "убыток до конца акции")  # 48 ₽ × 1 шт. × 5 дней
    assert r.link == f"/dashboard/products/promotions/{promo.id}" and r.key == f"promo:{p.product.id}:{promo.id}"
    no_sales = product(avg_daily=D("0"), promo=promo)
    assert rec.recommend(inputs(no_sales))[0].money is None  # не продаётся — убыток не оценить
    fine = product(promo=rec.PromoData(uuid.uuid4(), "Скидки", D("950"), D("3")))
    assert rec.recommend(inputs(fine)) == []


def test_supply_urgent_and_soon_one_per_product():
    a, b, c = product("A-1"), product("B-2"), product("C-3")
    lines = [
        rec.SupplyLine(a.product.id, "supply", "critical", "Москва", 30, D("3.5"), D("2.1")),
        rec.SupplyLine(a.product.id, "supply", "urgent", "Санкт-Петербург", 15, D("10"), D("1.2")),
        rec.SupplyLine(a.product.id, "supply", "planned", "Казань", 40, D("30"), D("1")),  # плановая — не торопим
        rec.SupplyLine(b.product.id, "purchase", "urgent", "Свой склад", 12, D("9"), D("0.8")),
        rec.SupplyLine(c.product.id, "supply", "critical", "Москва", 5, D("1"), D("1"), enough=False),  # мало отправлений
    ]
    recs = rec.recommend(inputs(a, b, c, supply=lines))
    assert [(r.title, r.kind) for r in recs] == [("Запланировать поставку A-1", "supply"), ("Закупить B-2", "supply")]
    assert recs[0].why == ("Срочно: в кластере «Москва» хватит на 3,5 дн. при продажах 2,1 шт. в день. Всего отправить "
                           "45 шт. — 2 направления.")
    assert recs[1].why.startswith("Скоро: на складе «Свой склад» хватит на 9 дн.") and "закупить 12 шт." in recs[1].why
    assert "запас после поставки 45 дн. и 7 дн. в пути" in recs[0].basis and recs[0].link == "/dashboard/warehouses/supply"


def test_missing_cost_one_for_shop_and_one_for_product():
    gaps = [CostGap("A-1", "Подшипник A", D("1500"), 3), CostGap("B-2", None, D("4000"), 2),
            CostGap("C-3", None, D("200"), 1), CostGap("D-4", None, D("100"), 1)]
    [r] = rec.recommend(inputs(cost_gaps=gaps))
    assert (r.key, r.title, r.link) == ("costs", "Заполнить себестоимость: 4 товара", "/dashboard/products/costs?missing=1")
    assert r.why == ("За 30 дней продано на 5 800 ₽ без себестоимости — прибыль, минимальная цена и маржа по этим товарам "
                     "не считаются (это не ноль). Больше всего: B-2 (4 000 ₽), A-1 (1 500 ₽), C-3 (200 ₽) и ещё 1. "
                     "Можно шаблоном Excel.")
    [one] = rec.recommend(inputs(cost_gaps=gaps[:1]))  # один товар — о нём одном
    assert (one.key, one.title) == ("costs", "Заполнить себестоимость A-1") and "продано 3 шт. на 1 500 ₽" in one.why
    no_cost = product("A-1", has_cost=False)
    [mine] = rec.recommend(inputs(no_cost, cost_gaps=gaps), no_cost.product.id)  # «Статистика товара»
    assert (mine.key, mine.title) == (f"cost:{no_cost.product.id}", "Заполнить себестоимость A-1")
    assert "продано 3 шт. на 1 500 ₽" in mine.why


def test_withdraw_idle_with_frozen_money():
    p = product("E-5")
    idle = [rec.IdleStock(p.product.id, 120, 100, 45, D("300"))]
    [r] = rec.recommend(inputs(p, idle=idle))
    assert r.title == "Вывезти E-5 со складов Ozon" and r.link == "/dashboard/warehouses/withdrawal"
    assert r.why == ("Продаж нет 45 дн. На складах 120 шт. — к вывозу 100 шт. на 30 000 ₽ по себестоимости — деньги "
                     "заморожены, а Ozon берёт за хранение.")
    assert (r.money, r.money_label) == (D("30000.00"), "заморожено в товаре") and "оставляем 2 шт." in r.basis
    never = rec.recommend(inputs(p, idle=[rec.IdleStock(p.product.id, 10, 8, None, None)]))[0]
    assert never.why == "Продаж в данных нет. На складах 10 шт. — к вывозу 8 шт." and never.money is None


# ── данных мало ──
def test_not_enough_data_no_recommendations_not_approximate():
    p = product("A-1", price=D("900"), min_price=D("1050"))
    gaps = [CostGap("Z-9", None, D("500"), 1)]
    assert rec.recommend(inputs(p, data_days=13, cost_gaps=gaps)) == []
    assert len(rec.recommend(inputs(p, data_days=14, cost_gaps=gaps))) == 2
    # нет цены или себестоимости — по товару ничего, кроме «заполнить себестоимость»
    no_price = product("B-2", price=None, min_price=D("500"))
    no_cost = product("C-3", has_cost=False, min_price=D("2000"), price=D("900"))
    idle = [rec.IdleStock(no_cost.product.id, 10, 8, None, None)]
    assert [r.kind for r in rec.recommend(inputs(no_price, no_cost, idle=idle, cost_gaps=gaps))] == [rec.COST]


def test_order_rules_then_money():
    small = product("A-1", price=D("900"), min_price=D("1000"), unit_profit=D("-10"), avg_daily=D("1"))
    big = product("B-2", price=D("900"), min_price=D("1000"), unit_profit=D("-50"), avg_daily=D("1"))
    promo = product("C-3", promo=rec.PromoData(uuid.uuid4(), "Акция", D("500"), D("-1")))
    soon = product("D-4")
    urgent = product("E-5")
    idle = product("F-6")
    lines = [rec.SupplyLine(soon.product.id, "supply", "urgent", "Москва", 5, D("10"), D("1")),
             rec.SupplyLine(urgent.product.id, "supply", "critical", "Москва", 5, D("2"), D("1"))]
    recs = rec.recommend(inputs(small, big, promo, soon, urgent, idle, supply=lines,
                                idle=[rec.IdleStock(idle.product.id, 10, 8, None, D("10"))],
                                cost_gaps=[CostGap("Z-9", None, D("500"), 1)]))
    assert [r.product.offer_id if r.product else r.kind for r in recs] == ["C-3", "B-2", "A-1", "E-5", "D-4",
                                                                           "missing_cost", "F-6"]


# ── API ──
def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_api_no_data_no_recommendations(client, tokens):
    empty = (await client.get("/api/v1/recommendations", headers=auth(tokens))).json()
    assert (empty["items"], empty["enough_data"], empty["data_days"], empty["min_days"]) == ([], False, 0, 14)


async def test_api_recommendations_hide_and_unhide(client, tokens, sessions):
    get = lambda **params: client.get("/api/v1/recommendations", params=params, headers=auth(tokens))  # noqa: E731
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)  # A — в акции в минус (Ozon добавил сам), B — цена ниже минимальной, C — без цены
    body = (await get()).json()
    assert body["enough_data"] and body["data_days"] == 30 and body["hide_days"] == 7
    kinds = [(i["kind"], i["product"]["offer_id"]) for i in body["items"]]
    assert kinds == [("promo_loss", "6001-2RS.C3"), ("price_below_min", "ACB305523")]
    price = body["items"][1]
    assert price["why"].startswith("Цена 200 ₽ ниже минимальной") and price["money_label"] == "убыток за 30 дней"
    assert Decimal(price["money"]) > 0 and price["link"] == f"/dashboard/products/prices?product={p['B']}"
    mine = (await get(product_id=str(p["B"]))).json()["items"]
    assert [i["kind"] for i in mine] == ["price_below_min"]  # «Статистика товара» (10.14)

    r = await client.patch("/api/v1/settings", json={"recommendation_hide_days": 3}, headers=auth(tokens))
    assert r.json()["recommendation_hide_days"] == 3
    hidden = await client.post("/api/v1/recommendations/hide", json={"key": price["key"]}, headers=auth(tokens))
    assert hidden.status_code == 200 and hidden.json()["hide_days"] == 3
    until = datetime.fromisoformat(hidden.json()["hidden_until"])
    assert timedelta(days=2, hours=23) < until - datetime.now(timezone.utc) <= timedelta(days=3)
    after = (await get()).json()
    assert [i["kind"] for i in after["items"]] == ["promo_loss"] and after["hidden"] == 1

    async with sessions() as db:  # срок вышел — условие осталось, рекомендация снова видна
        row = await db.scalar(select(RecommendationHide).where(RecommendationHide.key == price["key"]))
        row.hidden_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    assert (await get()).json()["hidden"] == 0

    await client.post("/api/v1/recommendations/hide", json={"key": price["key"]}, headers=auth(tokens))
    restored = await client.post("/api/v1/recommendations/unhide", headers=auth(tokens))
    assert restored.json() == {"restored": 1} and len((await get()).json()["items"]) == 2
    async with sessions() as db:  # записи не удаляются
        assert len(list((await db.execute(select(RecommendationHide))).scalars())) == 2

    bad = await client.patch("/api/v1/settings", json={"recommendation_hide_days": 0}, headers=auth(tokens))
    assert bad.status_code == 400
    assert (await client.get("/api/v1/recommendations")).status_code == 401
