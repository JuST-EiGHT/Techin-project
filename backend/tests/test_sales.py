"""Продажи по товарам по дням (шаг 3.5): пересборка из начислений, возвраты, пропуски, дни запаса."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select

from app.engine.product_pnl import LineIn
from app.engine.sales import aggregate, avg_daily
from app.models.product import Product
from app.models.sales_daily import SalesDaily
from app.services import sales as sales_service
from tests.test_pnl_api import add_api_day, user_id
from tests.test_report_pnl import WEEK, auth, upload
from tests.test_sync_finance import DAY, make_user, ozon, sync  # noqa: F401 — ozon: фикстура

D = date(2026, 9, 8)


def ln(type_name, amount, offer=None, qty=None, posting=None, day=D):
    return LineIn(day, type_name, Decimal(amount), qty, offer, "", "", posting)


def test_aggregate_sales_and_returns():
    rows, unattributed = aggregate([
        ln("Выручка", "1000", "A", 2, "P1"), ln("Выручка", "500", "A", 1, "P2"),
        ln("Возврат выручки", "-500", "A", 1, "P2"),
        ln("Вознаграждение за продажу", "-150", "A", 2),  # не продажа
        ln("Выручка", "300", None, None, "P3"),  # без артикула — по отправлению с одним товаром
        ln("Выручка", "99", None, 1),  # некуда отнести
        ln("Выручка", "700", "B", 1, day=date(2026, 9, 9)),
    ], {"P3": {"C"}})
    got = {(r.day, r.offer_id): (r.qty_sold, r.qty_returned, r.revenue, r.returns_amount) for r in rows}
    assert got == {
        (D, "A"): (3, 1, Decimal(1500), Decimal(500)),
        (D, "C"): (1, 0, Decimal(300), Decimal(0)),  # пусто — одна штука, как в отчёте
        (date(2026, 9, 9), "B"): (1, 0, Decimal(700), Decimal(0)),
    }
    assert unattributed == 1


def test_avg_daily_needs_enough_days():
    assert avg_daily(19, 7) == Decimal("2.714")
    assert avg_daily(19, 6) is None  # мало дней с данными — «нет данных», а не заниженное среднее
    assert avg_daily(-2, 10) == Decimal("0.000")  # возвратов больше продаж — не меньше нуля


async def daily(client, tokens, **params):
    r = await client.get("/api/v1/sales/daily", params=params, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_report_sales_by_day_and_gaps(client, tokens):
    await upload(client, tokens, WEEK)
    body = await daily(client, tokens, date_from="2026-09-05", date_to="2026-09-14")
    days = {d["date"]: d for d in body["days"]}
    assert [days[d]["source"] for d in ("2026-09-05", "2026-09-06", "2026-09-07", "2026-09-14")] == [
        None, None, "xlsx", None
    ]
    assert days["2026-09-05"] == {"date": "2026-09-05", "source": None, "qty_sold": None, "qty_returned": None,
                                  "revenue": None, "returns_amount": None}  # нет данных, а не 0
    t = body["totals"]
    assert (t["qty_sold"], t["qty_returned"], t["revenue"]) == (39, 3, "23712.03")  # как «Выручка» отчёта
    assert (t["days_with_data"], t["days_without_data"]) == (7, 3)
    one = await daily(client, tokens, offer_id="6004-2RS.CM.SRL.NAC.TH", date_from="2026-09-07",
                      date_to="2026-09-13")
    assert one["totals"]["qty_sold"] == 19 and one["totals"]["avg_daily"] == "2.714"
    # день с данными без продаж товара — 0, а не null
    assert any(d["source"] == "xlsx" and d["qty_sold"] == 0 for d in one["days"])


async def test_rebuild_follows_report_hide_and_restore(client, tokens, sessions):
    report = await upload(client, tokens, WEEK)
    uid = await user_id(client, tokens)

    async def rows():
        async with sessions() as db:
            return await db.scalar(select(func.count()).select_from(SalesDaily).where(SalesDaily.user_id == uid))

    built = await rows()
    assert built > 0
    await client.delete(f"/api/v1/reports/{report['id']}", headers=auth(tokens))
    assert await rows() == 0  # отчёт скрыт — продаж за его дни больше нет
    await client.post(f"/api/v1/reports/{report['id']}/restore", headers=auth(tokens))
    assert await rows() == built
    await client.post(f"/api/v1/reports/{report['id']}/reparse", headers=auth(tokens))
    assert await rows() == built  # перечитывание — без дублей


async def test_finance_sync_rebuilds_day_without_duplicates(sessions, ozon):  # noqa: F811
    uid = await make_user(sessions)
    await sync(sessions, uid, DAY, DAY)
    async with sessions() as db:
        body = await sales_service.daily(db, uid, DAY, DAY)
        first = (await db.execute(select(SalesDaily).where(SalesDaily.user_id == uid))).scalars().all()
    assert body["days"][0]["source"] == "api" and body["totals"]["qty_sold"] >= 1
    assert all(r.source == "api" and r.product_id is not None for r in first)
    await sync(sessions, uid, DAY, DAY)  # повторная синхронизация того же дня
    async with sessions() as db:
        again = (await db.execute(select(SalesDaily).where(SalesDaily.user_id == uid))).scalars().all()
    assert sorted((r.offer_id, r.qty_sold, r.revenue) for r in again) == sorted(
        (r.offer_id, r.qty_sold, r.revenue) for r in first
    )


async def test_avg_daily_by_product_for_days_of_supply(client, tokens, sessions):
    await upload(client, tokens, WEEK)
    uid = await user_id(client, tokens)
    async with sessions() as db:
        now = datetime.now(timezone.utc)
        sold = Product(user_id=uid, ozon_product_id=1, offer_id="6004-2RS.CM.SRL.NAC.TH", synced_at=now)
        idle = Product(user_id=uid, ozon_product_id=2, offer_id="NOT-SOLD", synced_at=now)
        db.add_all([sold, idle])
        await db.commit()
        avg = await sales_service.avg_daily_by_product(db, uid, today=date(2026, 9, 14))
        assert avg == {sold.id: Decimal("2.714"), idle.id: Decimal("0.000")}  # 19 шт. за 7 дней с данными
        # в окне 30 дней только 6 дней с данными — среднее не считаем
        assert await sales_service.avg_daily_by_product(db, uid, today=date(2026, 9, 13)) == {}


async def test_api_days_and_history_rebuild(client, tokens, sessions):
    uid = await user_id(client, tokens)
    await add_api_day(sessions, uid, date(2026, 9, 20), [("Выручка", "1000", 2, "A-1"), ("Возврат выручки", "-500", 1, "A-1")])
    # строки внесены в обход синхронизации, продаж ещё нет — история собирается при первом запросе
    body = await daily(client, tokens, date_from="2026-09-20", date_to="2026-09-20")
    assert body["days"][0] == {"date": "2026-09-20", "source": "api", "qty_sold": 2, "qty_returned": 1,
                               "revenue": "1000.00", "returns_amount": "500.00"}
    async with sessions() as db:
        await db.execute(delete(SalesDaily).where(SalesDaily.user_id == uid))
        await db.commit()
    r = await client.post("/api/v1/sales/rebuild", headers=auth(tokens))
    assert r.status_code == 200 and r.json()["rows"] == 1 and r.json()["days_with_data"] == 1


async def test_validation_and_isolation(client, tokens, sessions):
    bad = await client.get("/api/v1/sales/daily", params={"date_from": "2026-09-10", "date_to": "2026-09-01"},
                           headers=auth(tokens))
    assert (bad.status_code, bad.json()["detail"]) == (400, "Период: дата начала позже даты окончания")
    long = await client.get("/api/v1/sales/daily", params={"date_from": "2025-01-01", "date_to": "2026-09-01"},
                            headers=auth(tokens))
    assert long.status_code == 400
    other = await make_user(sessions, email="other@example.com", with_key=False)
    async with sessions() as db:
        foreign = (await db.execute(select(Product.id).where(Product.user_id == other))).scalars().first()
    r = await client.get("/api/v1/sales/daily", params={"product_id": str(foreign)}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (404, "Товар не найден")
    r = await client.get("/api/v1/sales/daily", params={"product_id": str(uuid.uuid4())}, headers=auth(tokens))
    assert r.status_code == 404
