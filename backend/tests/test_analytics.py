"""Шаг 10.3: основа аналитики — ряды и итоги по дням, неделям и месяцам, пропуски ≠ 0, сравнение, товар и категория,
заказы ≠ продажи, совпадение выручки и прибыли с P&L; справочник категорий Ozon."""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.engine import metrics as m
from app.integrations.ozon.errors import OzonServerError
from app.models.ozon_category import OzonCategory
from app.models.posting import FBO, Posting, PostingItem, PostingSync
from app.models.product import Product
from app.models.product_cost import ProductCost
from app.services import sales
from app.services.sync import categories, runner
from tests.test_pnl_api import add_api_day, user_id
from tests.test_report_pnl import OWNER, auth, set_profile

D = date
BEARINGS, AUTO, TIRES = 17028922, 17027484, 17028923
TREE = json.loads((Path(__file__).parent / "fixtures" / "ozon" / "description_category_tree.json").read_text())


# ── чистый модуль ──
def test_buckets_partial_week_and_month():
    assert m.buckets(D(2026, 9, 9), D(2026, 9, 22), "week") == [
        (D(2026, 9, 9), D(2026, 9, 13), True),  # среда — неделя обрезана
        (D(2026, 9, 14), D(2026, 9, 20), False),
        (D(2026, 9, 21), D(2026, 9, 22), True),
    ]
    assert m.buckets(D(2026, 8, 15), D(2026, 10, 10), "month") == [
        (D(2026, 8, 15), D(2026, 8, 31), True), (D(2026, 9, 1), D(2026, 9, 30), False), (D(2026, 10, 1), D(2026, 10, 10), True),
    ]
    assert len(m.buckets(D(2026, 2, 1), D(2026, 2, 28), "day")) == 28
    assert m.buckets(D(2026, 9, 7), D(2026, 9, 13), "week") == [(D(2026, 9, 7), D(2026, 9, 13), False)]
    with pytest.raises(ValueError):
        m.buckets(D(2026, 9, 7), D(2026, 9, 13), "year")


def day(revenue=None, profit=None, sold=None, orders=None, qty=None):
    dec = lambda v: None if v is None else Decimal(v)  # noqa: E731
    return m.DayData(revenue=dec(revenue), profit=dec(profit), margin_base=dec(revenue), sold_qty=sold,
                     returned_qty=None if sold is None else 0, returns_amount=None if sold is None else Decimal(0),
                     orders=orders, ordered_qty=qty)


def test_series_gaps_are_not_zero():
    days = {D(2026, 9, 7): day("1000", "300", 2, 1, 2), D(2026, 9, 8): day(orders=0, qty=0),  # нет начислений
            D(2026, 9, 9): day("500", "100", 1)}  # нет отправлений
    points = m.series(days, D(2026, 9, 7), D(2026, 9, 9), "day")
    assert [p["revenue"] for p in points] == [Decimal(1000), None, Decimal(500)]  # пропуск, а не 0
    assert [p["orders"] for p in points] == [1, 0, None]
    week = m.series(days, D(2026, 9, 7), D(2026, 9, 9), "week")[0]
    assert (week["revenue"], week["profit"], week["sold_qty"], week["orders"]) == (Decimal(1500), Decimal(400), 3, 1)
    assert (week["missing_days"], week["orders_missing_days"], week["partial"], week["days"]) == (1, 1, True, 3)
    assert week["margin"] == Decimal("26.67") and week["avg_price"] == Decimal("500.00")
    empty = m.series({}, D(2026, 9, 7), D(2026, 9, 7), "day")[0]
    assert empty["revenue"] is None and empty["margin"] is None and empty["avg_price"] is None


def test_changes_and_previous_alignment():
    cur = {"revenue": Decimal(1500), "profit": Decimal(300), "margin": Decimal(20), "orders": 4, "sold_qty": 0}
    prev = {"revenue": Decimal(1000), "profit": Decimal(0), "margin": Decimal("12.5"), "orders": None, "sold_qty": 0}
    c = m.changes(cur, prev)
    assert c["revenue"] == Decimal("50.0") and c["margin_pp"] == Decimal("7.5")
    assert c["profit"] is None and c["orders"] is None and c["sold_qty"] is None  # прошлая 0 или нет данных
    a = m.series({}, D(2026, 9, 7), D(2026, 9, 9), "day")
    b = m.series({}, D(2026, 8, 31), D(2026, 9, 1), "day")
    joined = m.with_previous(a, b)
    assert joined[0]["prev"]["date_from"] == D(2026, 8, 31) and joined[2]["prev"] is None


# ── данные магазина ──
async def setup_store(client, tokens, sessions) -> uuid.UUID:
    """Неделя 07–13.09.2026: начисления 07, 08, 10 (09 и 11–13 — нет), отправления загружены с 01.09 по 12.09."""
    uid = await user_id(client, tokens)
    await add_api_day(sessions, uid, D(2026, 9, 7), [
        ("Выручка", "1000", 2, "A"), ("Вознаграждение за продажу", "-200", 2, "A"),
        ("Выручка", "500", 1, "B"), ("Возврат выручки", "-250", 1, "B"),
    ])
    await add_api_day(sessions, uid, D(2026, 9, 8), [("Выручка", "500", 1, "A")])
    await add_api_day(sessions, uid, D(2026, 9, 10), [("Выручка", "600", 1, "B"), ("Логистика", "-50", None, "B")])
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        products = {p.offer_id: p for p in (await db.execute(select(Product).where(Product.user_id == uid))).scalars()}
        products["A"].category_id = BEARINGS  # у B категории нет
        for offer, cost in (("A", "100"), ("B", "200")):
            db.add(ProductCost(user_id=uid, offer_id=offer, cost_price=Decimal(cost), vat_multiplier=Decimal(1),
                               effective_from=D(2026, 1, 1)))
        db.add(PostingSync(user_id=uid, scheme=FBO, loaded_from=D(2026, 9, 1),
                           synced_at=datetime(2026, 9, 12, 12, tzinfo=timezone.utc)))
        for number, when, status, items in (
            ("1-1", D(2026, 9, 6), "delivered", [("A", 3)]),  # заказ в воскресенье — продажи по нему позже
            ("1-2", D(2026, 9, 7), "awaiting_deliver", [("A", 1), ("B", 1)]),
            ("1-3", D(2026, 9, 7), "cancelled", [("B", 5)]),  # отмена — не заказ
        ):
            posting = Posting(user_id=uid, scheme=FBO, posting_number=number, status=status, day=when,
                              ordered_at=datetime(when.year, when.month, when.day, 9, tzinfo=timezone.utc), synced_at=now)
            posting.items = [PostingItem(sku=1, offer_id=o, product_id=products[o].id, quantity=q) for o, q in items]
            db.add(posting)
        await sales.rebuild(db, uid, D(2026, 9, 1), D(2026, 9, 13))
        await db.commit()
    return uid


async def metrics(client, tokens, status=200, **params):
    query = {"date_from": "2026-09-07", "date_to": "2026-09-13", **params}
    r = await client.get("/api/v1/analytics/metrics", params=query, headers=auth(tokens))
    assert r.status_code == status, r.text
    return r.json()


async def product_ids(sessions, uid) -> dict[str, str]:
    async with sessions() as db:
        return {p.offer_id: str(p.id) for p in (await db.execute(select(Product).where(Product.user_id == uid))).scalars()}


async def test_store_by_day_matches_pnl(client, tokens, sessions):
    await setup_store(client, tokens, sessions)
    r = await metrics(client, tokens)
    pts = {p["date_from"]: p for p in r["points"]}
    assert [pts[f"2026-09-{d:02d}"]["revenue"] for d in range(7, 14)] == ["1500.00", "500.00", None, "600.00", None, None, None]
    assert pts["2026-09-07"]["sold_qty"] == 3 and pts["2026-09-07"]["returned_qty"] == 1
    assert pts["2026-09-07"]["returns_amount"] == "250.00" and pts["2026-09-09"]["sold_qty"] is None
    # заказы ≠ продажи: 08.09 — продажа без заказа, 13.09 — отправления не загружены
    assert [pts[f"2026-09-{d:02d}"]["orders"] for d in range(7, 14)] == [1, 0, 0, 0, 0, 0, None]
    assert pts["2026-09-07"]["ordered_qty"] == 2
    assert r["coverage"]["missing_days"] == 4 and r["coverage"]["orders_missing_days"] == 1

    # итоги — из P&L того же периода
    pnl = (await client.get("/api/v1/pnl", params={"date_from": "2026-09-07", "date_to": "2026-09-13"},
                            headers=auth(tokens))).json()
    t = r["totals"]
    assert t["revenue"] == f"{Decimal(pnl['revenue_base']):.2f}" == "2600.00"
    assert r["profit_kind"] == "before_tax" and pnl["pnl"]["net"] is None  # налоговый профиль не задан
    assert t["profit"] == f"{Decimal(pnl['pnl']['profit_before_tax']):.2f}"
    assert t["margin"] == f"{Decimal(t['profit']) / Decimal(t['revenue']) * 100:.2f}"
    assert (t["sold_qty"], t["orders"], t["ordered_qty"], t["avg_price"]) == (5, 1, 2, "520.00")
    # сумма прибыли по дням — прибыль до налога P&L по дням
    by_day = (await client.get("/api/v1/pnl/by-day", params={"date_from": "2026-09-07", "date_to": "2026-09-13"},
                               headers=auth(tokens))).json()
    assert [p["profit"] for p in r["points"]] == [
        None if d["profit_before_tax"] is None else f"{Decimal(d['profit_before_tax']):.2f}" for d in by_day["days"]]
    assert r["previous"] is None and r["changes"] is None


async def test_store_profit_after_tax_like_dashboard(client, tokens, sessions):
    await setup_store(client, tokens, sessions)
    await set_profile(client, tokens, OWNER)  # налог задан — прибыль магазина после налога, как на главной (3.7)
    r = await metrics(client, tokens, compare="true")
    pnl = (await client.get("/api/v1/pnl", params={"date_from": "2026-09-07", "date_to": "2026-09-13"},
                            headers=auth(tokens))).json()["pnl"]
    assert r["profit_kind"] == "net" and r["totals"]["profit"] == f"{Decimal(pnl['net']):.2f}"
    assert r["totals"]["margin"] == f"{Decimal(pnl['margin']):.2f}"  # маржинальность = прибыль / выручка — как в P&L
    in_series = sum(Decimal(p["profit"]) for p in r["points"] if p["profit"] is not None)
    assert in_series == Decimal(pnl["profit_before_tax"]) != Decimal(pnl["net"])  # в ряде — до налога: по дням он не делится
    dash = (await client.get("/api/v1/dashboard/ozon", params={"date_from": "2026-09-07", "date_to": "2026-09-13"},
                             headers=auth(tokens))).json()["figures"]
    assert Decimal(dash["profit"]["value"]) == Decimal(r["totals"]["profit"]) and dash["profit"]["kind"] == "net"
    assert Decimal(dash["revenue"]["value"]) == Decimal(r["totals"]["revenue"])


async def test_week_and_month_partial(client, tokens, sessions):
    await setup_store(client, tokens, sessions)
    r = await metrics(client, tokens, date_from="2026-09-02", date_to="2026-09-10", granularity="week")
    first, second = r["points"]
    assert (first["date_from"], first["date_to"], first["partial"], first["revenue"]) == ("2026-09-02", "2026-09-06", True, None)
    assert first["orders"] == 1 and first["missing_days"] == 5  # заказ есть, начислений нет
    assert (second["date_from"], second["date_to"], second["partial"], second["revenue"]) == (
        "2026-09-07", "2026-09-10", True, "2600.00")
    assert second["missing_days"] == 1 and second["days"] == 4
    r = await metrics(client, tokens, date_from="2026-08-25", date_to="2026-09-10", granularity="month")
    assert [(p["date_from"], p["date_to"], p["partial"], p["revenue"]) for p in r["points"]] == [
        ("2026-08-25", "2026-08-31", True, None), ("2026-09-01", "2026-09-10", True, "2600.00")]
    assert r["points"][0]["orders"] is None  # август: отправления не загружены


async def test_compare_with_previous_period(client, tokens, sessions):
    await setup_store(client, tokens, sessions)
    r = await metrics(client, tokens, compare="true")
    assert (r["previous"]["date_from"], r["previous"]["date_to"]) == ("2026-08-31", "2026-09-06")
    assert r["previous"]["totals"]["revenue"] is None and r["compare_note"].startswith("За прошлый период данных нет")
    assert r["previous"]["totals"]["orders"] == 1 and r["previous"]["totals"]["orders_missing_days"] == 1
    assert r["points"][0]["prev"]["date_from"] == "2026-08-31" and r["points"][0]["prev"]["orders"] is None
    assert r["points"][6]["prev"]["orders"] == 1  # 06.09 — заказ
    assert r["changes"]["revenue"] is None  # сравнивать не с чем
    # оба дня с данными: 08.09 против 07.09
    r = await metrics(client, tokens, date_from="2026-09-08", date_to="2026-09-08", compare="true")
    assert r["previous"]["totals"]["revenue"] == "1500.00" and r["changes"]["revenue"] == "-66.7"
    assert r["changes"]["sold_qty"] == "-66.7" and r["compare_note"] is None


async def test_product_filter_matches_product_pnl(client, tokens, sessions):
    uid = await setup_store(client, tokens, sessions)
    ids = await product_ids(sessions, uid)
    r = await metrics(client, tokens, product_id=ids["A"])
    pts = {p["date_from"]: p for p in r["points"]}
    assert pts["2026-09-07"]["revenue"] == "1000.00" and pts["2026-09-07"]["profit"] == "600.00"  # 1000 − 200 − 100×2
    assert pts["2026-09-10"]["revenue"] == "0.00" and pts["2026-09-09"]["revenue"] is None  # нет продаж ≠ нет данных
    assert pts["2026-09-07"]["orders"] == 1 and pts["2026-09-07"]["ordered_qty"] == 1
    assert r["filter"]["products"][0]["offer_id"] == "A" and r["filter"]["product_count"] == 1
    by_product = (await client.get("/api/v1/pnl/by-product", params={"date_from": "2026-09-07", "date_to": "2026-09-13"},
                                   headers=auth(tokens))).json()
    a = next(i for i in by_product["items"] if i["offer_id"] == "A")
    t = r["totals"]
    assert (t["revenue"], t["profit"], t["margin"]) == (a["revenue"], a["profit"], a["margin"])
    assert t["sold_qty"] == a["qty"] == 3 and r["profit_kind"] == "before_tax"
    # два товара — сумма
    r = await metrics(client, tokens, product_id=[ids["A"], ids["B"]])
    assert r["totals"]["revenue"] == "2600.00" and r["filter"]["product_count"] == 2


async def test_category_filter_and_list(client, tokens, sessions):
    uid = await setup_store(client, tokens, sessions)
    async with sessions() as db:
        db.add(OzonCategory(id=BEARINGS, name="Подшипники", path="Автотовары / Подшипники", parent_id=AUTO,
                            updated_at=datetime.now(timezone.utc)))
        await db.commit()
    ids = await product_ids(sessions, uid)
    by_category = await metrics(client, tokens, category_id=BEARINGS)
    by_product = await metrics(client, tokens, product_id=ids["A"])
    assert by_category["totals"] == by_product["totals"] and by_category["points"] == by_product["points"]
    assert by_category["filter"]["category"] == {"id": BEARINGS, "name": "Подшипники", "path": "Автотовары / Подшипники"}
    no_category = await metrics(client, tokens, category_id=0)
    assert no_category["totals"]["revenue"] == "1100.00" and no_category["filter"]["category"]["name"] == "Без категории"
    both = await metrics(client, tokens, category_id=BEARINGS, product_id=ids["B"])  # B не из этой категории
    assert both["filter"]["product_count"] == 0 and both["totals"]["revenue"] == "0.00"

    r = await client.get("/api/v1/analytics/categories", headers=auth(tokens))
    assert r.json() == [
        {"id": BEARINGS, "name": "Подшипники", "path": "Автотовары / Подшипники", "products": 1},
        {"id": 0, "name": "Без категории", "path": None, "products": 1},
    ]
    r = await client.get("/api/v1/analytics/products", params={"search": "a"}, headers=auth(tokens))  # артикул или название
    assert [p["offer_id"] for p in r.json()] == ["A"]
    r = await client.get("/api/v1/analytics/products", headers=auth(tokens))
    assert [p["offer_id"] for p in r.json()] == ["A", "B"]
    r = await client.get("/api/v1/analytics/products", params={"id": ids["B"]}, headers=auth(tokens))
    assert [p["offer_id"] for p in r.json()] == ["B"]


async def test_validation_and_store_isolation(client, tokens, sessions):
    uid = await setup_store(client, tokens, sessions)
    assert "Детализация" in (await metrics(client, tokens, status=400, granularity="year"))["detail"]
    await metrics(client, tokens, status=400, date_from="2026-09-13", date_to="2026-09-07")
    await metrics(client, tokens, status=400, date_from="2025-01-01", date_to="2026-09-07")  # больше 366 дней
    assert "не найден" in (await metrics(client, tokens, status=404, product_id=str(uuid.uuid4())))["detail"]
    # второй магазин аккаунта: своих данных нет, товар первого — не найден
    async with sessions() as db:
        from app.models.user import User

        store = User(owner_id=uid, store_name="Второй")
        db.add(store)
        await db.commit()
        store_id = str(store.id)
    other = {**auth(tokens), "X-Store-Id": store_id}
    r = await client.get("/api/v1/analytics/metrics", params={"date_from": "2026-09-07", "date_to": "2026-09-13"},
                         headers=other)
    assert r.json()["totals"]["revenue"] is None and r.json()["coverage"]["missing_days"] == 7
    ids = await product_ids(sessions, uid)
    r = await client.get("/api/v1/analytics/metrics", headers=other,
                         params={"date_from": "2026-09-07", "date_to": "2026-09-13", "product_id": ids["A"]})
    assert r.status_code == 404


async def test_cache_reset_on_new_data(client, tokens, sessions):
    uid = await setup_store(client, tokens, sessions)
    assert (await metrics(client, tokens))["totals"]["revenue"] == "2600.00"
    await add_api_day(sessions, uid, D(2026, 9, 11), [("Выручка", "400", 1, "A")])
    from app.services.pnl_stale import invalidate

    async with sessions() as db:  # так делает синхронизация начислений
        await invalidate(db, uid, D(2026, 9, 11), D(2026, 9, 11), "начисления")
        await db.commit()
    assert (await metrics(client, tokens))["totals"]["revenue"] == "3000.00"


# ── справочник категорий ──
class FakeClient:
    def __init__(self, response=TREE, error=None):
        self.calls, self.response, self.error = [], response, error

    async def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        if self.error:
            raise self.error
        return self.response


async def test_category_tree_saves_needed_with_path(client, tokens, sessions):
    uid = await setup_store(client, tokens, sessions)
    fake = FakeClient()
    async with sessions() as db:
        stats = await categories.refresh(db, uid, fake)
    assert fake.calls == [("POST", "/v1/description-category/tree", {"language": "DEFAULT"})]
    assert stats == {"categories": 1, "categories_unknown": 0, "categories_loaded": True}
    async with sessions() as db:
        rows = {c.id: c for c in (await db.execute(select(OzonCategory))).scalars()}
    assert set(rows) == {BEARINGS, AUTO}  # категория товара и её родитель, остальной каталог не храним
    assert (rows[BEARINGS].name, rows[BEARINGS].path, rows[BEARINGS].parent_id) == ("Подшипники", "Автотовары / Подшипники", AUTO)
    async with sessions() as db:  # всё известно и свежо — Ozon не спрашиваем
        assert (await categories.refresh(db, uid, fake))["categories_loaded"] is False
    assert len(fake.calls) == 1
    async with sessions() as db:  # справочник устарел — обновляем
        await db.execute(update(OzonCategory).values(updated_at=datetime.now(timezone.utc) - timedelta(days=40)))
        await db.commit()
        assert (await categories.refresh(db, uid, fake))["categories_loaded"] is True
    async with sessions() as db:  # категории нет в дереве — считаем, не падаем
        await db.execute(update(Product).where(Product.offer_id == "B").values(category_id=999))
        await db.commit()
        stats = await categories.refresh(db, uid, fake)
    assert stats["categories_unknown"] == 1


def test_walk_skips_types_and_bad_nodes():
    tree = categories.walk(TREE["result"] + [{"type_id": 1, "type_name": "тип"}, "мусор", {"description_category_id": 5}])
    assert tree[TIRES] == ("Шины", AUTO, "Автотовары / Шины") and 970836453 not in tree and 5 not in tree
    assert categories.walk(None) == {}


async def test_category_error_does_not_fail_products_sync(sessions, monkeypatch):
    class Stats:
        def as_dict(self):
            return {"created": 1}

    async def products(*_):
        return Stats()

    async def broken(*_):
        raise OzonServerError()

    monkeypatch.setattr(runner, "sync_products", products)
    monkeypatch.setattr(runner, "sync_categories", broken)
    async with sessions() as db:
        stats = await runner._products_job(db, uuid.uuid4(), object())
    assert stats["created"] == 1 and "не отвечает" in stats["categories_error"]
