"""Страница товаров (шаг 4.1): расчёты, список с фильтрами и сортировкой, CSV, карточка товара."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.engine import products as engine
from app.models.accrual import AccrualDay
from app.models.product import Product, ProductPrice
from app.models.product_cost import BEGINNING, ProductCost
from app.models.promotion import Promotion, PromotionProduct
from app.models.sales_daily import SalesDaily
from app.models.warehouse import ProductStock, Warehouse
from app.services.sync.finance import MSK, today_msk
from tests.test_pricing import add_sales

D = Decimal


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ── расчёты ──
@pytest.mark.parametrize("raw,level", [
    ("GREEN", "green"), ("COLOR_INDEX_RED", "red"), ("yellow", "yellow"), ("SUPER", "super"),
    ("COLOR_INDEX_SUPER", "super"), ("WITHOUT_INDEX", None), ("COLOR_INDEX_WITHOUT_INDEX", None), ("", None), (None, None),
    ("PURPLE", None),
])
def test_price_index_level(raw, level):
    assert engine.price_index_level(raw) == level


def test_percent_of():
    assert engine.percent_of(D("459"), D("38")) == D("174.42")  # комиссия 38 % от 459 ₽
    assert engine.percent_of(None, D("38")) is None and engine.percent_of(D("459"), None) is None


def test_acquiring_pct():
    # Ozon отдаёт эквайринг в рублях при текущей цене: 19,8 ₽ при 1320 ₽ — это 1,5 %, а не 19,8 %
    assert engine.acquiring_pct(D("19.8"), D("1320")) == D("1.500")
    assert engine.acquiring_pct(None, D("1320")) is None and engine.acquiring_pct(D("19.8"), D("0")) is None


def test_positive_price():
    assert engine.positive(D("0")) is None and engine.positive(None) is None and engine.positive(D("1")) == D("1")


def test_daily_prices():
    d = date(2026, 10, 1)
    snaps = [
        engine.PricePoint(date(2026, 10, 5), D("450")),
        engine.PricePoint(date(2026, 10, 3), D("500"), D("400")),
        engine.PricePoint(date(2026, 10, 5), D("440")),  # тот же день, записан позже — он и действует
    ]
    days = engine.daily_prices(snaps, d, date(2026, 10, 6))
    assert [x["price"] for x in days] == [None, None, D("500"), D("500"), D("440"), D("440")]  # до первого — нет истории
    assert days[2]["min_price"] == D("400") and days[4]["min_price"] is None
    # снимок до начала периода — цена с первого дня
    before = engine.daily_prices([engine.PricePoint(date(2026, 9, 1), D("300"))], d, date(2026, 10, 2))
    assert [x["price"] for x in before] == [D("300"), D("300")]
    # цена 0 у Ozon — «не задана»
    assert engine.daily_prices([engine.PricePoint(d, D("0"))], d, d)[0]["price"] is None


def test_search_filters_sort():
    assert engine.matches("подшипник ё", "Подшипник Е 6001") and engine.matches("6001", "6001-2RS")
    assert engine.matches("  ", "что угодно") and not engine.matches("7000", "6001", None)
    rows = [
        {"offer_id": "b", "margin": D("5"), "stock_status": "low", "promotions": [1], "cost_price": D("1")},
        {"offer_id": "a", "margin": None, "stock_status": None, "promotions": [], "cost_price": None},
        {"offer_id": "c", "margin": D("-1"), "stock_status": "critical", "promotions": [], "cost_price": D("1")},
        {"offer_id": "d", "margin": D("5"), "stock_status": "normal", "promotions": [], "cost_price": D("1")},
    ]
    pick = lambda f: [r["offer_id"] for r in rows if engine.passes(r, f)]  # noqa: E731
    assert pick({"negative"}) == ["c"] and pick({"low_stock"}) == ["b", "c"] and pick({"critical_stock"}) == ["c"]
    assert pick({"in_promo"}) == ["b"] and pick({"no_cost"}) == ["a"] and pick({"low_stock", "in_promo"}) == ["b"]
    assert pick(set()) == ["b", "a", "c", "d"]
    # пустые — в конце в обе стороны; при равных — по артикулу
    assert [r["offer_id"] for r in engine.sort_rows(rows, "margin", "desc")] == ["b", "d", "c", "a"]
    assert [r["offer_id"] for r in engine.sort_rows(rows, "margin", "asc")] == ["c", "b", "d", "a"]


# ── данные ──
async def user_id_of(client, tokens) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])


def at_msk(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), MSK).astimezone(timezone.utc)


async def seed(sessions, user_id: uuid.UUID, *, sales: bool = True) -> dict[str, uuid.UUID]:
    """Каталог: A — в плюс, в акции, остаток на двух складах; B — в минус, кончился; C — без себестоимости и цены;
    E — в архиве. Продажи — 30 дней данных до вчера."""
    today = today_msk()
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        p = {
            key: Product(user_id=user_id, ozon_product_id=n, ozon_sku=900000 + n, offer_id=offer, name=name,
                         is_archived=key == "E", synced_at=now, barcode="4600000000001" if key == "A" else None)
            for key, n, offer, name in [
                ("A", 1, "6001-2RS.C3", "Подшипник шариковый 6001"), ("B", 2, "ACB305523", "Подшипник ступицы"),
                ("C", 3, "6000-ZZ", "Подшипник 6000 ёлочный"), ("E", 4, "OLD-1", "Снят с продажи"),
            ]
        }
        db.add_all(p.values())
        await db.flush()
        db.add_all([
            # A: цена была 500, 10 дней назад стала 459
            ProductPrice(product_id=p["A"].id, snapshot_at=at_msk(today - timedelta(days=40)), price=D("500"),
                         min_price=D("400"), old_price=D("600"), commission_pct=D("38"), acquiring=D("7.5"),
                         price_index="YELLOW"),
            ProductPrice(product_id=p["A"].id, snapshot_at=at_msk(today - timedelta(days=10)), price=D("459"),
                         min_price=D("400"), old_price=D("600"), marketing_price=D("449"), commission_pct=D("38"),
                         acquiring=D("6.885"), price_index="COLOR_INDEX_GREEN"),  # эквайринг 1,5 % от 459 ₽
            ProductPrice(product_id=p["B"].id, snapshot_at=at_msk(today - timedelta(days=3)), price=D("200"),
                         min_price=D("0"), commission_pct=D("38"), price_index="RED"),
            ProductPrice(product_id=p["E"].id, snapshot_at=at_msk(today - timedelta(days=3)), price=D("100"),
                         commission_pct=D("38")),
        ])
        db.add_all([
            ProductCost(user_id=user_id, offer_id=offer, cost_price=D("164"), vat_multiplier=D("1.22"),
                        effective_from=BEGINNING, source="manual", created_at=now)
            for offer in ("6001-2RS.C3", "ACB305523", "OLD-1")
        ])
        w1 = Warehouse(user_id=user_id, ozon_warehouse_id=1, name="Хоругвино", type="FBO", cluster_name="Москва",
                       synced_at=now)
        w2 = Warehouse(user_id=user_id, ozon_warehouse_id=2, name="Шушары", type="FBO", cluster_name="Санкт-Петербург",
                       synced_at=now)
        db.add_all([w1, w2])
        await db.flush()
        db.add_all([
            ProductStock(product_id=p["A"].id, warehouse_id=w1.id, snapshot_at=now, present=5, in_transit=2),
            ProductStock(product_id=p["A"].id, warehouse_id=w2.id, snapshot_at=now, present=10, in_transit=0),
            ProductStock(product_id=p["B"].id, warehouse_id=w1.id, snapshot_at=now - timedelta(days=1), present=3),
            ProductStock(product_id=p["B"].id, warehouse_id=w1.id, snapshot_at=now, present=0),
        ])
        future = now + timedelta(days=5)
        active = Promotion(user_id=user_id, ozon_action_id=1, title="Осенняя распродажа", date_to=future,
                           is_participating=True, synced_at=now)
        finished = Promotion(user_id=user_id, ozon_action_id=2, title="Летняя", date_to=now - timedelta(days=1),
                             is_participating=True, synced_at=now)
        db.add_all([active, finished])
        await db.flush()
        db.add_all([
            PromotionProduct(promotion_id=active.id, product_id=p["A"].id, action_price=D("420"), is_active=True,
                             margin_in_promo=D("12.5"), added_automatically=True, first_seen_at=now, synced_at=now),
            PromotionProduct(promotion_id=active.id, product_id=p["B"].id, action_price=D("150"), is_active=False,
                             first_seen_at=now, synced_at=now),  # кандидат — не участвует
            PromotionProduct(promotion_id=finished.id, product_id=p["B"].id, action_price=D("150"), is_active=True,
                             first_seen_at=now, synced_at=now),  # акция закончилась
        ])
        if sales:
            for i in range(1, 31):
                day = today - timedelta(days=i)
                db.add(AccrualDay(user_id=user_id, day=day, synced_at=now, entries=1))
                db.add(SalesDaily(user_id=user_id, day=day, offer_id="6001-2RS.C3", product_id=p["A"].id, qty_sold=1,
                                  qty_returned=0, revenue=D("459"), returns_amount=D("0"), source="api", calculated_at=now))
            db.add(SalesDaily(user_id=user_id, day=today - timedelta(days=4), offer_id="ACB305523", product_id=p["B"].id,
                              qty_sold=6, qty_returned=0, revenue=D("1200"), returns_amount=D("0"), source="api",
                              calculated_at=now))
            db.add(SalesDaily(user_id=user_id, day=today - timedelta(days=2), offer_id="ACB305523", product_id=p["B"].id,
                              qty_sold=0, qty_returned=1, revenue=D("0"), returns_amount=D("200"), source="api",
                              calculated_at=now))  # возврат — не продажа
            # начисления — для логистики на штуку в калькуляторе цены (шаг 4.2): A — 60 ₽, B — 30 ₽
            await add_sales(db, user_id, p["A"], today - timedelta(days=1), price=D("459"), logistics=D("60"))
            await add_sales(db, user_id, p["B"], today - timedelta(days=4), qty=6, price=D("200"), logistics=D("30"))
        await db.commit()
        return {k: v.id for k, v in p.items()}


async def get_page(client, tokens, **params):
    r = await client.get("/api/v1/products", params=params, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


# ── список ──
async def test_list_rows_and_counts(client, tokens, sessions):
    ids = await seed(sessions, await user_id_of(client, tokens))
    data = await get_page(client, tokens)
    assert [r["offer_id"] for r in data["items"]] == ["6000-ZZ", "6001-2RS.C3", "ACB305523"]  # без архива, по артикулу
    assert data["total"] == 3 and data["sales_known"] and data["stocks_known"] and data["sales_window_days"] == 30
    assert data["counts"] == {"all": 3, "no_cost": 1, "in_promo": 1, "critical_stock": 1, "low_stock": 1, "negative": 1,
                              "archived": 1}
    assert "калькуляторе цены" in data["margin_note"] and "логистика" in data["margin_note"]
    rows = {r["offer_id"]: r for r in data["items"]}
    a = rows["6001-2RS.C3"]
    assert a["id"] == str(ids["A"]) and a["ozon_sku"] == 900001
    assert (a["price"], a["min_price"], a["old_price"], a["marketing_price"]) == ("459.00", "400.00", "600.00", "449.00")
    assert (a["commission_pct"], a["commission"], a["acquiring_pct"]) == ("38.000", "174.42", "1.500")
    # калькулятор цены: 459 × (1 − 0,38 − 0,015) − (200,08 + логистика 60) = 17,615 ₽ → 3,84 %
    assert (a["cost_price"], a["cost_with_vat"], a["unit_profit"], a["margin"]) == ("164.00", "200.08", "17.62", "3.84")
    assert a["margin_level"] == "low" and a["price_index"] == "green"
    # остаток на двух складах 15, в пути 2; продано 30 за 30 дней → 1 в день → 15 дней запаса
    assert (a["stock"], a["in_transit"], a["avg_daily_sales"], a["days_of_supply"], a["stock_status"]) == (
        15, 2, "1.000", "15.0", "normal")
    assert [p["title"] for p in a["promotions"]] == ["Осенняя распродажа"]
    assert a["promotions"][0]["action_price"] == "420.00" and a["promotions"][0]["added_automatically"]
    assert a["last_sale"] == (today_msk() - timedelta(days=1)).isoformat()
    b = rows["ACB305523"]
    # кончился (последний снимок 0) при продажах → критично; мин. цена 0 у Ozon — «не задана»; кандидат и прошедшая акция — не в акции
    assert (b["stock"], b["stock_status"], b["days_of_supply"]) == (0, "critical", "0.0")
    # 200 × 0,62 − (200,08 + 30) = −106,08 ₽ → −53,04 %
    assert b["min_price"] is None and b["margin"] == "-53.04" and b["margin_level"] == "negative" and b["price_index"] == "red"
    assert b["promotions"] == [] and b["last_sale"] == (today_msk() - timedelta(days=4)).isoformat()
    c = rows["6000-ZZ"]
    # ни цены, ни себестоимости, ни снимков остатка (остатки загружались — значит 0), не продавался → не «нет данных»
    assert c["price"] is None and c["cost_price"] is None and c["margin"] is None and c["commission"] is None
    assert c["stock"] == 0 and c["in_transit"] is None and c["stock_status"] is None and c["last_sale"] is None


async def test_no_data_is_not_zero(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        db.add(Product(user_id=uid, ozon_product_id=7, offer_id="X-1", synced_at=now))
        await db.commit()
    data = await get_page(client, tokens)
    row = data["items"][0]
    assert not data["sales_known"] and not data["stocks_known"]
    assert row["stock"] is None and row["avg_daily_sales"] is None and row["days_of_supply"] is None
    assert data["counts"]["no_cost"] == 1 and data["counts"]["critical_stock"] == 0


async def test_empty_catalog(client, tokens):
    data = await get_page(client, tokens)
    assert data["items"] == [] and data["total"] == 0 and data["counts"]["all"] == 0


async def test_filters_search_archived(client, tokens, sessions):
    await seed(sessions, await user_id_of(client, tokens))
    offers = lambda d: [r["offer_id"] for r in d["items"]]  # noqa: E731
    assert offers(await get_page(client, tokens, filters="negative")) == ["ACB305523"]
    assert offers(await get_page(client, tokens, filters="low_stock")) == ["ACB305523"]
    assert offers(await get_page(client, tokens, filters="critical_stock")) == ["ACB305523"]
    assert offers(await get_page(client, tokens, filters="in_promo")) == ["6001-2RS.C3"]
    assert offers(await get_page(client, tokens, filters="no_cost")) == ["6000-ZZ"]
    both = await get_page(client, tokens, filters="in_promo, negative")
    assert both["items"] == [] and both["counts"]["all"] == 3  # счётчики — без фильтров
    # поиск: регистр, «ё» = «е», SKU и ID товара
    assert offers(await get_page(client, tokens, search="ЕЛОЧНЫЙ")) == ["6000-ZZ"]
    assert offers(await get_page(client, tokens, search="900002")) == ["ACB305523"]
    assert offers(await get_page(client, tokens, search="подшипник", filters="in_promo")) == ["6001-2RS.C3"]
    archived = await get_page(client, tokens, archived=True)
    assert offers(archived) == ["OLD-1"] and archived["counts"]["all"] == 1 and archived["counts"]["archived"] == 1
    r = await client.get("/api/v1/products", params={"filters": "cheap"}, headers=auth(tokens))
    assert r.status_code == 400 and "Неизвестный фильтр: cheap" in r.json()["detail"]


async def test_sort_and_pages(client, tokens, sessions):
    await seed(sessions, await user_id_of(client, tokens))
    offers = lambda d: [r["offer_id"] for r in d["items"]]  # noqa: E731
    assert offers(await get_page(client, tokens, sort="margin", order="desc")) == ["6001-2RS.C3", "ACB305523", "6000-ZZ"]
    assert offers(await get_page(client, tokens, sort="margin", order="asc")) == ["ACB305523", "6001-2RS.C3", "6000-ZZ"]
    assert offers(await get_page(client, tokens, sort="price", order="asc")) == ["ACB305523", "6001-2RS.C3", "6000-ZZ"]
    assert offers(await get_page(client, tokens, sort="stock", order="desc")) == ["6001-2RS.C3", "6000-ZZ", "ACB305523"]
    assert offers(await get_page(client, tokens, sort="index", order="desc")) == ["ACB305523", "6001-2RS.C3", "6000-ZZ"]
    assert offers(await get_page(client, tokens, sort="last_sale", order="desc")) == ["6001-2RS.C3", "ACB305523", "6000-ZZ"]
    assert offers(await get_page(client, tokens, sort="name")) == ["6000-ZZ", "ACB305523", "6001-2RS.C3"]
    page = await get_page(client, tokens, limit=2, offset=2)
    assert offers(page) == ["ACB305523"] and page["total"] == 3 and (page["limit"], page["offset"]) == (2, 2)
    r = await client.get("/api/v1/products", params={"sort": "color"}, headers=auth(tokens))
    assert r.status_code == 400


async def test_export_csv(client, tokens, sessions):
    await seed(sessions, await user_id_of(client, tokens))
    r = await client.get("/api/v1/products/export", params={"sort": "margin", "order": "asc"}, headers=auth(tokens))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "filename*=UTF-8''" in r.headers["content-disposition"] and "products.csv" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("﻿Артикул;Название;SKU;Цена, руб.")
    lines = text.strip("﻿").splitlines()
    assert len(lines) == 4 and lines[1].startswith("ACB305523;Подшипник ступицы;900002;200,00;")
    a = lines[2].split(";")
    assert a[0] == "6001-2RS.C3" and "3,84" in a and "выгодный" in a and "Осенняя распродажа" in a and "норма" in a
    assert (today_msk() - timedelta(days=1)).strftime("%d.%m.%Y") in a
    only = await client.get("/api/v1/products/export", params={"filters": "no_cost"}, headers=auth(tokens))
    assert len(only.content.decode("utf-8").strip().splitlines()) == 2


# ── карточка ──
async def test_detail(client, tokens, sessions):
    ids = await seed(sessions, await user_id_of(client, tokens))
    r = await client.get(f"/api/v1/products/{ids['A']}", headers=auth(tokens))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["item"]["offer_id"] == "6001-2RS.C3" and d["item"]["margin"] == "3.84" and d["barcode"] == "4600000000001"
    assert d["cost"] == {"cost_price": "164.00", "vat_multiplier": "1.220", "supplier_vat": "1.220",
                         "cost_with_vat": "200.08", "effective_from": BEGINNING.isoformat(), "note": None, "history_count": 1}
    assert d["supplier_vat"] == "1.220"  # нет налогового профиля — общая ставка 22 %
    # склады: больше — выше; в пути показан
    assert [(s["warehouse"], s["present"], s["in_transit"]) for s in d["stocks"]] == [("Шушары", 10, 0), ("Хоругвино", 5, 2)]
    # цена за 30 дней: снимок 40 дней назад действует с начала периода, 10 дней назад — новая
    days = d["price_days"]
    assert len(days) == 30 and days[-1]["date"] == today_msk().isoformat()
    assert [x["price"] for x in days[:19]] == ["500.00"] * 19 and [x["price"] for x in days[19:]] == ["459.00"] * 11
    assert days[-1]["marketing_price"] == "449.00" and days[0]["marketing_price"] is None
    # изменения внутри периода — новые первыми; первый снимок — 40 дней назад
    assert [c["price"] for c in d["price_changes"]] == ["459.00"]
    assert d["first_price_at"].startswith((today_msk() - timedelta(days=40)).isoformat())
    week = (await client.get(f"/api/v1/products/{ids['A']}", params={"days": 7}, headers=auth(tokens))).json()
    assert len(week["price_days"]) == 7 and week["price_changes"] == []
    # у C ничего нет — пустые блоки, а не ошибки
    c = (await client.get(f"/api/v1/products/{ids['C']}", headers=auth(tokens))).json()
    assert c["cost"] is None and c["stocks"] == [] and c["price_changes"] == [] and c["first_price_at"] is None
    assert all(x["price"] is None for x in c["price_days"])


async def test_detail_after_cost_change(client, tokens, sessions):
    """Правка себестоимости в боковой панели — существующий PUT /costs: строка товара видит новую цену."""
    ids = await seed(sessions, await user_id_of(client, tokens))
    r = await client.put("/api/v1/costs/6000-ZZ", json={"cost_price": "100", "vat_multiplier": "1"}, headers=auth(tokens))
    assert r.status_code == 200
    d = (await client.get(f"/api/v1/products/{ids['C']}", headers=auth(tokens))).json()
    assert d["item"]["cost_price"] == "100.00" and d["item"]["cost_with_vat"] == "100.00" and d["cost"]["history_count"] == 1
    assert (await get_page(client, tokens))["counts"]["no_cost"] == 0


async def test_foreign_and_missing_product(client, tokens, sessions):
    ids = await seed(sessions, await user_id_of(client, tokens))
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    r = await client.get(f"/api/v1/products/{ids['A']}", headers=auth(t2))
    assert r.status_code == 404 and r.json()["detail"] == "Товар не найден"
    assert (await client.get(f"/api/v1/products/{uuid.uuid4()}", headers=auth(tokens))).status_code == 404
    assert (await get_page(client, t2))["items"] == []
    assert (await client.get("/api/v1/products")).status_code == 401


async def test_user_without_sales_data(client, tokens, sessions):
    await seed(sessions, await user_id_of(client, tokens), sales=False)
    data = await get_page(client, tokens)
    a = next(r for r in data["items"] if r["offer_id"] == "6001-2RS.C3")
    assert not data["sales_known"] and a["stock"] == 15 and a["days_of_supply"] is None and a["stock_status"] is None
    assert data["counts"]["critical_stock"] == 0 and a["last_sale"] is None
