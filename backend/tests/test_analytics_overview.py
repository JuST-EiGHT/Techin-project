"""Шаг 10.7: «Обзор бизнеса» — лучшие и худшие товары по прибыли и по изменению продаж к прошлому периоду (только
значимые изменения — порог шума, как в аномалиях 7.1), фильтры товара и категории."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import update

from app.engine import leaders as lead
from app.models.accrual import AccrualDay
from app.models.product import Product
from app.models.sales_daily import SalesDaily
from app.services.sync.finance import today_msk
from tests.test_products import seed, user_id_of

D = Decimal


def row(offer: str, profit: str | None, revenue: str = "1000") -> lead.ProfitRow:
    return lead.ProfitRow(offer, None, None, D(revenue), None if profit is None else D(profit), None, 1)


def test_by_profit_best_positive_worst_from_the_rest():
    rows = [row("A", "1000"), row("B", "500"), row("C", "-200"), row("D", None), row("E", "100")]
    best, worst = lead.by_profit(rows)
    assert [r.offer_id for r in best] == ["A", "B", "E"] and [r.offer_id for r in worst] == ["C"]  # без себестоимости — нет
    best, worst = lead.by_profit(rows, limit=2)
    assert [r.offer_id for r in best] == ["A", "B"] and [r.offer_id for r in worst] == ["C", "E"]
    assert lead.by_profit([row("X", "-5"), row("Y", "0")]) == ([], [row("X", "-5"), row("Y", "0")])  # всё в минусе


def test_sales_changes_only_significant():
    current = {"DROP": 40, "NOISE": 3, "NEW3": 3, "NEW5": 5, "UP": 30, "GONE": 0}
    previous = {"DROP": 100, "NOISE": 2, "UP": 10, "GONE": 8}
    growth, decline = lead.sales_changes(current, previous, 30, 30)
    assert [(c.offer_id, c.change_pct) for c in growth] == [("UP", D("200.0")), ("NEW5", None)]  # по изменению в штуках
    assert [(c.offer_id, c.change_pct) for c in decline] == [("DROP", D("-60.0")), ("GONE", D("-100.0"))]
    # 3 → 2 — случайность; с нуля 3 шт. — тоже (от 4 шт.)
    assert "NOISE" not in {c.offer_id for c in growth + decline} and "NEW3" not in {c.offer_id for c in growth}
    # дней с данными в прошлом периоде вдвое меньше — ожидаем вдвое больше
    [up], [] = lead.sales_changes({"X": 40}, {"X": 10}, 30, 15)
    assert (up.expected, up.change_pct) == (D("20.0"), D("100.0"))
    assert lead.sales_changes({"X": 40}, {"X": 10}, 30, 6) is None  # меньше 7 дней — не сравниваем


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_api_leaders(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)  # A — в плюс, 1 шт. в день 30 дней; B — в минус, 6 шт. за период; C — без продаж
    today, now = today_msk(), datetime.now(timezone.utc)
    async with sessions() as db:  # прошлый период: A продавался по 5 шт. в день
        for i in range(31, 61):
            day = today - timedelta(days=i)
            db.add(AccrualDay(user_id=uid, day=day, synced_at=now, entries=1))
            db.add(SalesDaily(user_id=uid, day=day, offer_id="6001-2RS.C3", product_id=p["A"], qty_sold=5, qty_returned=0,
                              revenue=D("2295"), returns_amount=D(0), source="api", calculated_at=now))
        await db.execute(update(Product).where(Product.id == p["B"]).values(category_id=17028922))
        await db.commit()
    period = {"date_from": str(today - timedelta(days=30)), "date_to": str(today - timedelta(days=1))}
    get = lambda **kw: client.get("/api/v1/analytics/leaders", params={**period, **kw}, headers=auth(tokens))  # noqa: E731

    body = (await get()).json()
    assert body["previous"] == {"date_from": str(today - timedelta(days=60)), "date_to": str(today - timedelta(days=31))}
    profit = body["profit"]
    assert [(i["product"]["offer_id"], i["product"]["id"]) for i in profit["best"]] == [("6001-2RS.C3", str(p["A"]))]
    assert [i["product"]["offer_id"] for i in profit["worst"]] == ["ACB305523"] and D(profit["worst"][0]["profit"]) < 0
    sales = body["sales"]
    assert sales["compared"] and (sales["days"], sales["prev_days"]) == (30, 30)
    assert [(i["product"]["offer_id"], i["qty"], i["prev_qty"], i["change_pct"]) for i in sales["decline"]] == [
        ("6001-2RS.C3", 30, 150, "-80.00")]
    assert [(i["product"]["offer_id"], i["change_pct"]) for i in sales["growth"]] == [("ACB305523", None)]  # с нуля

    only_b = (await get(category_id=17028922)).json()  # категория — только её товары
    assert only_b["profit"]["best"] == [] and [i["product"]["offer_id"] for i in only_b["profit"]["worst"]] == ["ACB305523"]
    assert only_b["sales"]["decline"] == []
    assert (await get(product_id=str(p["A"]))).json()["profit"]["best"][0]["product"]["offer_id"] == "6001-2RS.C3"

    week = {"date_from": str(today - timedelta(days=3)), "date_to": str(today - timedelta(days=1))}
    short = (await client.get("/api/v1/analytics/leaders", params=week, headers=auth(tokens))).json()["sales"]
    assert not short["compared"] and "не меньше 7 дней" in short["note"]  # мало дней — не сравниваем

    assert (await get(product_id=str(uuid.uuid4()))).status_code == 404
    bad = await client.get("/api/v1/analytics/leaders", params={"date_from": period["date_to"], "date_to": period["date_from"]},
                           headers=auth(tokens))
    assert bad.status_code == 400
