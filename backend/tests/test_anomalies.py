"""Аномалии (шаг 7.1): поиск по данным сервиса, жизненный цикл записей, проверки денег, Celery и API."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select, update

from app.models.accrual import AccrualDay
from app.models.anomaly import Anomaly
from app.models.bank import BankTransaction
from app.models.ozon_balance import OzonBalanceDay
from app.models.planning import MoneyAccount, MoneyOperation
from app.models.price_change import PriceChange
from app.models.product import Product, ProductPrice
from app.models.sales_daily import SalesDaily
from app.models.sync_run import SyncRun
from app.models.warehouse import ProductStock, Warehouse
from app.services import anomalies as svc
from app.services import ozon_payouts, planning
from app.services.sync.finance import today_msk
from app.services.sync.runner import run_sync
from app.tasks import sync as tasks
from tests.test_payouts import WEEKS, add_days, history
from tests.test_products import seed, user_id_of
from tests.test_sync_finance import make_user

D = Decimal


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def detect(sessions, uid) -> dict:
    async with sessions() as db:
        return await svc.detect(db, uid)


async def rows(sessions, uid, type_: str | None = None) -> list[Anomaly]:
    async with sessions() as db:
        q = select(Anomaly).where(Anomaly.user_id == uid).order_by(Anomaly.detected_at, Anomaly.severity)
        if type_:
            q = q.where(Anomaly.type == type_)
        return list((await db.execute(q)).scalars())


def active(items: list[Anomaly]) -> list[Anomaly]:
    return [a for a in items if a.resolved_at is None]


async def add_stock(sessions, product_id, **by_warehouse: int):
    """Новый снимок остатка товара на складах (по названию склада)."""
    async with sessions() as db:
        now = datetime.now(timezone.utc)
        for name, present in by_warehouse.items():
            wid = await db.scalar(select(Warehouse.id).where(Warehouse.name == name))
            db.add(ProductStock(product_id=product_id, warehouse_id=wid, snapshot_at=now, present=present))
        await db.commit()


# ── товары: маржа, остатки, акции ──
async def test_detect_products_and_no_duplicates(client, tokens, sessions):
    """Каталог страницы товаров: A — маржа 3,8 % (ниже 10 %), в акции автоматически и в минус; B — маржа −53 %, остатка
    нет при продажах; C — посчитать нечем; E — в архиве. Повторная проверка — без дублей."""
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    stats = await detect(sessions, uid)
    assert (stats["created"], stats["resolved"], stats["failed"]) == (5, 0, [])
    found = {(a.type, a.product_id, a.severity) for a in await rows(sessions, uid)}
    assert found == {
        ("low_margin", p["A"], "red"), ("low_margin", p["B"], "red"), ("low_stock", p["B"], "red"),
        ("promo_auto_added", p["A"], "yellow"), ("promo_loss", p["A"], "red"),
    }
    stock = (await rows(sessions, uid, "low_stock"))[0]
    assert (stock.metric_after, stock.threshold, stock.details["available"]) == (D("0"), D("7"), 0)
    margin = next(a for a in await rows(sessions, uid, "low_margin") if a.product_id == p["A"])
    assert (margin.metric_after, margin.threshold) == (D("3.84"), D("10"))

    again = await detect(sessions, uid)
    assert (again["created"], again["resolved"], again["active"]) == (0, 0, 5)  # активная того же типа — не дубль
    assert len(await rows(sessions, uid)) == 5


async def test_resolved_then_new_record_and_escalation(client, tokens, sessions):
    """Закрытие — resolved_at, повторное появление — новая запись; жёлтая → красная — новая запись, жёлтая в истории."""
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    await detect(sessions, uid)
    await add_stock(sessions, p["B"], Хоругвино=100)  # B снова на складе — запаса на 500 дней
    stats = await detect(sessions, uid)
    assert stats["resolved"] == 1
    [b] = await rows(sessions, uid, "low_stock")
    assert b.resolved_at is not None and b.resolution == "normal"
    await add_stock(sessions, p["B"], Хоругвино=0)
    await detect(sessions, uid)
    b_rows = await rows(sessions, uid, "low_stock")
    assert len(b_rows) == 2 and len(active(b_rows)) == 1  # закрытая осталась, новая — отдельная запись

    # A продаёт 1 шт. в день: 10 шт. — «Мало» (жёлтая), 3 шт. — «Критично» (красная)
    await add_stock(sessions, p["A"], Хоругвино=0, Шушары=10)
    await detect(sessions, uid)
    a_rows = [a for a in await rows(sessions, uid, "low_stock") if a.product_id == p["A"]]
    assert [(a.severity, a.threshold) for a in a_rows] == [("yellow", D("14"))]
    await add_stock(sessions, p["A"], Шушары=3)
    stats = await detect(sessions, uid)
    assert stats["escalated"] == 1
    a_rows = [a for a in await rows(sessions, uid, "low_stock") if a.product_id == p["A"]]
    assert [(a.severity, a.resolution) for a in a_rows] == [("yellow", "escalated"), ("red", None)]


async def test_archived_product_and_threshold_setting(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    await detect(sessions, uid)
    # порог «низкой маржи» — настройка: при 3 % маржа A (3,84 %) уже не низкая
    await client.patch("/api/v1/settings", json={"low_margin_pct": "3"}, headers=auth(tokens))
    await detect(sessions, uid)
    margins = {a.product_id: a for a in await rows(sessions, uid, "low_margin")}
    assert margins[p["A"]].resolution == "normal" and margins[p["B"]].resolved_at is None
    # товар убрали в архив — его аномалии больше не относятся к делу
    async with sessions() as db:
        await db.execute(update(Product).where(Product.id == p["B"]).values(is_archived=True))
        await db.commit()
    await detect(sessions, uid)
    b = [a for a in await rows(sessions, uid) if a.product_id == p["B"]]
    assert {(a.type, a.resolution) for a in b} == {("low_margin", "gone"), ("low_stock", "gone")}


async def test_failed_check_keeps_its_anomalies(client, tokens, sessions, monkeypatch):
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    await detect(sessions, uid)

    async def broken(db, ctx):
        raise RuntimeError("сбой")

    monkeypatch.setattr(svc, "GATHERERS", (("products", broken), *svc.GATHERERS[1:]))
    stats = await detect(sessions, uid)
    assert stats["failed"] == ["products"] and stats["resolved"] == 0
    assert len(active(await rows(sessions, uid))) == 5  # сбой проверки — не «всё в норме»


# ── продажи: дни без данных ≠ 0 ──
async def seed_sales(sessions, uid, recent: int, days_with_data: int = 35) -> uuid.UUID:
    """Товар X на складе (50 шт.): 10 продаж в день, последние 3 дня — recent в день. Дни загружены (AccrualDay)."""
    today, now = today_msk(), datetime.now(timezone.utc)
    async with sessions() as db:
        x = Product(user_id=uid, ozon_product_id=77, offer_id="X-1", name="Подшипник X", synced_at=now)
        w = Warehouse(user_id=uid, ozon_warehouse_id=7, name="Склад", type="FBO", synced_at=now)
        db.add_all([x, w])
        await db.flush()
        db.add(ProductStock(product_id=x.id, warehouse_id=w.id, snapshot_at=now, present=50))
        for i in range(1, days_with_data + 1):
            day = today - timedelta(days=i)
            db.add(AccrualDay(user_id=uid, day=day, synced_at=now, entries=1))
            qty = recent if i <= 3 else 10
            db.add(SalesDaily(user_id=uid, day=day, offer_id="X-1", product_id=x.id, qty_sold=qty, qty_returned=0,
                              revenue=D(qty * 100), returns_amount=D(0), source="api", calculated_at=now))
        await db.commit()
        return x.id


async def test_sales_drop_and_days_without_data(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    x = await seed_sales(sessions, uid, recent=1)
    await detect(sessions, uid)
    drops = await rows(sessions, uid, "sales_drop")
    assert {(a.key, a.severity) for a in drops} == {("shop", "red"), (f"product:{x}", "red")}
    shop = next(a for a in drops if a.key == "shop")
    assert (shop.metric_before, shop.metric_after, shop.details["change_pct"]) == (D("10"), D("1"), -90.0)

    # последние дни перестали загружаться: это «нет данных», а не «0 продаж» — аномалии не закрываются и не множатся
    today = today_msk()
    async with sessions() as db:
        gap = [today - timedelta(days=i) for i in (1, 2)]
        await db.execute(delete(AccrualDay).where(AccrualDay.user_id == uid, AccrualDay.day.in_(gap)))
        await db.execute(delete(SalesDaily).where(SalesDaily.user_id == uid, SalesDaily.day.in_(gap)))
        await db.commit()
    stats = await detect(sessions, uid)
    assert (stats["created"], stats["resolved"]) == (0, 0)
    assert len(active(await rows(sessions, uid, "sales_drop"))) == 2


async def test_no_data_is_not_a_drop(client, tokens, sessions):
    """Синхронизация не шла последние три дня — продажи неизвестны, падения нет."""
    uid = await user_id_of(client, tokens)
    await seed_sales(sessions, uid, recent=10)
    async with sessions() as db:
        today = today_msk()
        last = [today - timedelta(days=i) for i in (1, 2, 3)]
        await db.execute(delete(AccrualDay).where(AccrualDay.user_id == uid, AccrualDay.day.in_(last)))
        await db.execute(delete(SalesDaily).where(SalesDaily.user_id == uid, SalesDaily.day.in_(last)))
        await db.commit()
    await detect(sessions, uid)
    assert await rows(sessions, uid, "sales_drop") == []


# ── цена и комиссия ──
async def test_price_and_commission_change(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        x = Product(user_id=uid, ozon_product_id=5, offer_id="P-5", name="Подшипник P", synced_at=now)
        db.add(x)
        await db.flush()
        db.add_all([
            ProductPrice(product_id=x.id, snapshot_at=now - timedelta(days=30), price=D(900), commission_pct=D(15)),
            ProductPrice(product_id=x.id, snapshot_at=now - timedelta(days=10), price=D(1000), commission_pct=D(15)),
            ProductPrice(product_id=x.id, snapshot_at=now - timedelta(days=1), price=D(1400), commission_pct=D("17.5")),
            PriceChange(user_id=uid, product_id=x.id, offer_id="P-5", created_at=now - timedelta(days=1),
                        change_type="manual_single", price_before=D(1000), price_after=D(1400), status="applied",
                        is_applied=True),
        ])
        await db.commit()
    await detect(sessions, uid)
    [price] = await rows(sessions, uid, "price_change")
    assert (price.severity, price.metric_before, price.metric_after, price.threshold) == ("red", D(1000), D(1400), D(30))
    assert price.details["via_service"] is True  # изменение через сервис — тоже аномалия (решение владельца)
    [commission] = await rows(sessions, uid, "commission_change")
    assert (commission.severity, commission.metric_before, commission.metric_after) == ("yellow", D(15), D("17.5"))

    async with sessions() as db:  # цена и комиссия вернулись
        db.add(ProductPrice(product_id=x.id, snapshot_at=datetime.now(timezone.utc), price=D(1000), commission_pct=D(15)))
        await db.commit()
    await detect(sessions, uid)
    assert {a.resolution for a in await rows(sessions, uid)} == {"normal"}


# ── выплаты Ozon (без банка) ──
async def test_payout_missed_and_diff(client, tokens, sessions, monkeypatch):
    today = date(2026, 10, 14)  # среда
    for module in (svc, ozon_payouts):
        monkeypatch.setattr(module, "today_msk", lambda: today)
    uid = await user_id_of(client, tokens)
    weeks = {w: v * 7 for w, v in WEEKS.items()}
    facts = {p.day: p.amount for p in history(weeks, until=today)}
    missed = date(2026, 10, 7)
    facts.pop(missed)  # выплата 07.10 не пришла
    facts[date(2026, 9, 30)] = (facts[date(2026, 9, 30)] * D("0.8")).quantize(D("0.01"))  # пришло на 20 % меньше
    await add_days(sessions, uid, facts, weeks, today)
    stats = await detect(sessions, uid)
    assert stats["failed"] == []
    [late] = await rows(sessions, uid, "payout_missed")
    assert (late.key, late.severity, late.metric_before) == ("payout:2026-10-07", "red", D("84000"))  # неделя продаж 14.09: 12 000 × 7
    [diff] = await rows(sessions, uid, "payout_diff")
    assert (diff.key, diff.severity, diff.details["change_pct"]) == ("payout:2026-09-30", "yellow", -20.0)

    # выплату провели с опозданием — просрочка закрывается
    async with sessions() as db:
        await db.execute(update(OzonBalanceDay).where(OzonBalanceDay.user_id == uid, OzonBalanceDay.day == date(2026, 10, 9))
                         .values(payments=D(-77000)))
        await db.commit()
    await detect(sessions, uid)
    [late] = await rows(sessions, uid, "payout_missed")
    assert late.resolution == "normal"


# ── «Планирование»: банк, кассовый разрыв, операции без статьи ──
async def setup_planning(client, tokens, balance="10000"):
    r = await client.post("/api/v1/planning/setup", headers=auth(tokens), json={
        "supplier_payment": "upfront", "accounts": [{"name": "Расчётный счёт", "kind": "checking", "balance": balance}],
        "ozon_account": 0, "categories": ["rent"]})
    assert r.status_code == 200, r.text
    return (await client.get("/api/v1/planning/accounts", headers=auth(tokens))).json()[0]["id"]


async def test_planning_checks_only_when_enabled(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    today = today_msk()
    account = await setup_planning(client, tokens)
    r = await client.post("/api/v1/planning/plans", headers=auth(tokens), json={
        "kind": "expense", "amount": "50000", "start_date": (today + timedelta(days=3)).isoformat(),
        "category_name": "Закупка", "account_id": account})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/planning/operations", headers=auth(tokens), json={
        "type": "income", "amount": "1000", "day": today.isoformat(), "account_id": account})
    assert r.status_code == 201, r.text
    async with sessions() as db:  # операция без статьи появилась 10 дней назад
        await db.execute(update(MoneyOperation).where(MoneyOperation.user_id == uid)
                         .values(created_at=datetime.now(timezone.utc) - timedelta(days=10)))
        await db.commit()
    await detect(sessions, uid)
    [gap] = await rows(sessions, uid, "cash_gap")
    assert (gap.severity, gap.metric_after, gap.details["first_day"]) == (
        "red", D("-39000"), (today + timedelta(days=3)).isoformat())
    [ops] = await rows(sessions, uid, "uncategorized")
    assert (ops.severity, ops.metric_after, ops.details["count"]) == ("yellow", D(1), 1)

    # планирование выключили — его проверки больше не относятся к делу
    r = await client.put("/api/v1/planning/settings", headers=auth(tokens), json={"enabled": False})
    assert r.status_code == 200, r.text
    await detect(sessions, uid)
    assert {(a.type, a.resolution) for a in await rows(sessions, uid)} == {("cash_gap", "gone"), ("uncategorized", "gone")}


async def test_bank_reconciliation(client, tokens, sessions):
    """Выплата Ozon есть, выписка за эти дни загружена, поступления нет — красная; пришло с разницей — жёлтая."""
    uid = await user_id_of(client, tokens)
    today = today_msk()
    account = uuid.UUID(await setup_planning(client, tokens, balance="100000"))
    payout_day = today - timedelta(days=20)
    async with sessions() as db:
        now = datetime.now(timezone.utc)
        db.add(OzonBalanceDay(user_id=uid, day=payout_day, accrued=D(0), payments=D(-15000), synced_at=now))
        db.add(BankTransaction(user_id=uid, account_id=account, day=today - timedelta(days=2), amount=D(-500),
                               dedup_key="rent-1", source="file_1c", kind="regular", raw_json={}))
        await db.commit()
    await detect(sessions, uid)
    [missing] = await rows(sessions, uid, "bank_missing")
    assert (missing.key, missing.severity, missing.metric_before) == (f"payout:{payout_day}", "red", D("15000.00"))

    async with sessions() as db:  # поступление нашлось, но на 50 ₽ меньше
        await planning.sync_ozon_operations(db, uid)
        op = await db.scalar(select(MoneyOperation).where(MoneyOperation.external_key == f"ozon_payout:{payout_day}"))
        db.add(BankTransaction(user_id=uid, account_id=account, day=payout_day + timedelta(days=1), amount=D(14950),
                               dedup_key="ozon-1", source="file_1c", kind="ozon", op_id=op.op_id, raw_json={}))
        await db.commit()
    await detect(sessions, uid)
    assert (await rows(sessions, uid, "bank_missing"))[0].resolution == "normal"
    [diff] = await rows(sessions, uid, "bank_diff")
    assert (diff.severity, diff.metric_before, diff.metric_after) == ("yellow", D("15000.00"), D("14950.00"))


async def test_no_planning_no_money_checks_from_planning(client, tokens, sessions):
    """Без «Планирования» банк, кассовый разрыв и операции без статьи не проверяются; без банка — тоже ничего."""
    uid = await user_id_of(client, tokens)
    await detect(sessions, uid)
    assert await rows(sessions, uid) == []
    async with sessions() as db:
        assert await db.scalar(select(MoneyAccount.id).where(MoneyAccount.user_id == uid)) is None


# ── API ──
async def test_api_list_count_acknowledge(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    await run_sync("anomalies", uid, sessions)  # как по расписанию: запуск в журнале
    get = lambda **params: client.get("/api/v1/anomalies", params=params, headers=auth(tokens))  # noqa: E731

    body = (await get()).json()
    assert body["total"] == 5 and body["counts"] == {"active": 5, "unread": 5, "red": 4, "yellow": 1,
                                                     "red_active": 4, "yellow_active": 1}
    assert body["checked_at"] is not None
    assert [i["severity"] for i in body["items"]] == ["red"] * 4 + ["yellow"]  # красные первыми
    item = next(i for i in body["items"] if i["type"] == "low_stock")
    assert item["title"] == "Товар закончился" and item["type_title"] == "Заканчивается остаток"
    assert item["product"]["offer_id"] == "ACB305523" and item["unit"] == "дн." and item["group"] == "stock"
    assert item["link"] == f"/dashboard/products?product={p['B']}"

    assert (await get(severity="yellow")).json()["total"] == 1
    assert (await get(group="promo")).json()["total"] == 2
    assert (await get(product_id=str(p["B"]))).json()["total"] == 2
    assert (await get(type="nonsense")).status_code == 400

    r = await client.post(f"/api/v1/anomalies/{item['id']}/acknowledge", headers=auth(tokens))
    assert r.status_code == 200 and r.json()["is_acknowledged"] is True and r.json()["resolved_at"] is None
    count = (await client.get("/api/v1/anomalies/count", headers=auth(tokens))).json()
    assert count == {"active": 5, "unread": 4, "red": 3, "yellow": 1, "red_active": 4, "yellow_active": 1}  # «Ок» — активна
    assert (await get(acknowledged="false")).json()["total"] == 4
    again = await client.post(f"/api/v1/anomalies/{item['id']}/acknowledge", headers=auth(tokens))
    assert again.json()["acknowledged_at"] == r.json()["acknowledged_at"]  # повторно — без изменений

    await add_stock(sessions, p["B"], Хоругвино=5)  # 25 дней запаса — норма (100 шт. — уже «Избыток», 10.5)
    await run_sync("anomalies", uid, sessions)
    resolved = (await get(status="resolved")).json()
    assert resolved["total"] == 1 and resolved["items"][0]["resolution"] == "normal"
    assert (await get(status="all")).json()["total"] == 5

    assert (await client.post(f"/api/v1/anomalies/{uuid.uuid4()}/acknowledge", headers=auth(tokens))).status_code == 404
    assert (await client.get("/api/v1/anomalies/count")).status_code == 401


async def test_other_users_anomalies_are_hidden(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    await detect(sessions, uid)
    a = (await rows(sessions, uid))[0]
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    assert (await client.get("/api/v1/anomalies", headers=auth(other))).json()["total"] == 0
    r = await client.post(f"/api/v1/anomalies/{a.id}/acknowledge", headers=auth(other))
    assert (r.status_code, r.json()["detail"]) == (404, "Аномалия не найдена")


# ── расписание ──
async def test_who_is_checked_and_journal(sessions):
    keyed = await make_user(sessions)
    no_key = await make_user(sessions, email="nokey@example.com", with_key=False)
    async with sessions() as db:
        assert await tasks.users_for_anomalies(db) == [keyed]
        from app.models.planning import PlanningSettings

        db.add(PlanningSettings(user_id=no_key, enabled=True))
        await db.commit()
        assert set(await tasks.users_for_anomalies(db)) == {keyed, no_key}  # деньги проверяем и без ключа Ozon
    run = await run_sync("anomalies", no_key, sessions)
    assert run.status == "success" and run.stats["created"] == 0
    async with sessions() as db:
        assert await db.scalar(select(SyncRun.kind).where(SyncRun.user_id == no_key)) == "anomalies"


def test_fanout(monkeypatch):
    users = [uuid.uuid4(), uuid.uuid4()]

    async def fake_users():
        return users

    queued = []
    monkeypatch.setattr(tasks, "_anomaly_users", fake_users)
    monkeypatch.setattr(tasks.detect_anomalies_for_user, "delay", lambda uid: queued.append(uid))
    assert tasks.detect_anomalies_all() == 2 and queued == [str(u) for u in users]
    assert tasks.FOR_USER_TASKS["anomalies"] is tasks.detect_anomalies_for_user


async def test_api_bell_order_groups_and_acknowledge_all(client, tokens, sessions):
    """Шаг 7.2: колокольчик — новые первыми; числа у фильтров по группам; «Отметить все прочитанными» — по фильтрам."""
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    await detect(sessions, uid)
    async with sessions() as db:  # самая свежая — жёлтая: в колокольчике она первая, на странице — после красных
        newest = (await db.execute(select(Anomaly).where(Anomaly.type == "promo_auto_added"))).scalar_one()
        newest.detected_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        await db.commit()
    get = lambda **params: client.get("/api/v1/anomalies", params=params, headers=auth(tokens))  # noqa: E731

    bell = (await get(order="new", limit=5)).json()
    assert bell["items"][0]["type"] == "promo_auto_added" and len(bell["items"]) == 5
    assert (await get()).json()["items"][0]["severity"] == "red"
    page = (await get(group="promo")).json()
    assert page["total"] == 2
    assert page["by_group"] == {"margin": 2, "stock": 1, "promo": 2}  # без фильтра группы — числа для всех групп
    assert (await get(severity="yellow")).json()["by_group"] == {"promo": 1}
    assert (await get(order="sideways")).status_code == 400

    r = await client.post("/api/v1/anomalies/acknowledge-all", json={"group": "margin"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["acknowledged"] == 2
    assert r.json()["counts"] == {"active": 5, "unread": 3, "red": 2, "yellow": 1, "red_active": 4, "yellow_active": 1}
    r = await client.post("/api/v1/anomalies/acknowledge-all", json={"product_id": str(p["A"])}, headers=auth(tokens))
    assert r.json()["acknowledged"] == 2  # промо-аномалии A; маржа A уже отмечена
    r = await client.post("/api/v1/anomalies/acknowledge-all", json={}, headers=auth(tokens))
    assert r.json()["acknowledged"] == 1 and r.json()["counts"]["unread"] == 0
    assert len(active(await rows(sessions, uid))) == 5  # «прочитано» — не «закрыто»
    bad = await client.post("/api/v1/anomalies/acknowledge-all", json={"type": "nonsense"}, headers=auth(tokens))
    assert bad.status_code == 400
