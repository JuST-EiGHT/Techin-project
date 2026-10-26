"""Шаг 10.5: «Требует внимания» — новые проверки (синхронизация, пропуски начислений, отчёт с ошибкой, продажи без
себестоимости, избыток запаса), деньги под риском, порядок списка, категория «Данные» в Telegram, пороги-настройки."""
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select, update

from app.engine import anomaly_detector as det
from app.engine import notifications as notify
from app.engine.anomaly_text import describe
from app.models.accrual import AccrualDay
from app.models.anomaly import Anomaly
from app.models.product import Product
from app.models.product_cost import ProductCost
from app.models.report import ERROR, READY, Report
from app.models.sales_daily import SalesDaily
from app.models.sync_run import FAILED, SUCCESS, SyncRun
from app.models.warehouse import ProductStock, Warehouse
from app.services.sync.finance import today_msk
from app.services.sync.overview import OZON_KINDS
from tests.test_anomalies import active, auth, detect, rows
from tests.test_sync_overview import add_key, add_run, me

D = Decimal
NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
TH = det.Thresholds()
H = timedelta(hours=1)


# ── синхронизация ──
def sync_items() -> list[det.SyncItem]:
    key = ("key", "Ozon не принял ключ: он истёк, отключён или неверный", "Замените ключ в «Интеграциях».",
           "/dashboard/settings/integrations")
    return [
        det.SyncItem("products", "Товары и цены", "error", NOW - H, *key, action_needed=True),
        det.SyncItem("stocks", "Остатки", "error", NOW - 2 * H, *key, action_needed=True),
        det.SyncItem("finance", "Начисления", "error", NOW - 3 * H, "ozon_down", "Ozon временно не отвечает"),  # разовый
        det.SyncItem("balance", "Баланс и выплаты", "stale", NOW - 30 * H, None, "Давно не обновлялись"),
        det.SyncItem("postings", "Отправления", "never", None, None, "Ещё не загружались", since=NOW - 2 * H),
        det.SyncItem("promotions", "Акции", "running", NOW - 40 * H),
        det.SyncItem("bank", "Банк", "error", NOW - H, "bank_token", "Банк не принял токен", "Выпустите новый токен.",
                     "/dashboard/settings/integrations?tab=banks", action_needed=True, ozon=False),
    ]


def test_sync_problem_red_now_yellow_after_threshold_one_item_per_key_problem():
    items = sync_items()
    check = det.sync_problem(items, NOW, TH, active={"sync:ozon:no_key"})
    found = {f.key: f for f in check.findings}
    assert {k: f.severity for k, f in found.items()} == {"sync:ozon:key": "red", "sync:balance": "yellow", "sync:bank": "red"}
    assert found["sync:ozon:key"].details["kinds"] == ["products", "stocks"]  # одна причина — один пункт
    assert found["sync:ozon:key"].details["last_success_at"] == (NOW - 2 * H).isoformat()  # самые старые из них
    assert check.live and {"sync:promotions", "sync:ozon:no_key"} <= check.keep  # идёт загрузка — не судим

    text = describe(det.SYNC_PROBLEM, "red", None, None, None, found["sync:ozon:key"].details)
    assert text.title == "Ozon не принял ключ — не обновляются товары и цены, остатки"
    assert text.message.startswith("Ozon не принял ключ: он истёк, отключён или неверный. Замените ключ")
    assert text.link == "/dashboard/settings/integrations"
    stale = describe(det.SYNC_PROBLEM, "yellow", None, None, None, found["sync:balance"].details)
    assert stale.title == "Баланс и выплаты: давно не обновлялись" and stale.link == "/dashboard?sync=1"  # окно синхронизации

    # ключ у одного вида — пункт этого вида; первая загрузка — от подключения; порог — настройка
    assert [f.key for f in det.sync_problem(items[:1], NOW, TH).findings] == ["sync:products"]
    late = det.sync_problem([replace(items[4], since=NOW - 25 * H)], NOW, TH)
    assert [(f.key, f.severity) for f in late.findings] == [("sync:postings", "yellow")]
    assert det.sync_problem([items[3]], NOW, replace(TH, sync_stale_hours=48)).findings == []
    assert det.sync_problem([replace(items[2], last_success_at=NOW - 25 * H)], NOW, TH).findings[0].severity == "yellow"


async def test_sync_problem_detected_and_closed(client, tokens, sessions):
    uid = await me(client, tokens)
    await add_key(sessions, uid)
    for kind in OZON_KINDS:
        await add_run(sessions, uid, kind, FAILED, ago=timedelta(minutes=30), error="Ozon не принял ключ",
                      stats={"error_kind": "key"})
    await detect(sessions, uid)
    [a] = active(await rows(sessions, uid, det.SYNC_PROBLEM))
    assert (a.key, a.severity, len(a.details["kinds"]), a.details["all_ozon"]) == ("sync:ozon:key", "red", 7, True)
    assert describe(a.type, a.severity, None, None, None, a.details).title == "Ozon не принял ключ — данные Ozon не обновляются"

    for kind in OZON_KINDS:  # ключ заменили — всё загрузилось
        await add_run(sessions, uid, kind, SUCCESS, ago=timedelta(minutes=5))
    await detect(sessions, uid)
    assert (await rows(sessions, uid, det.SYNC_PROBLEM))[0].resolution == "normal"

    await add_run(sessions, uid, "postings", SUCCESS, ago=timedelta(hours=30))  # отправления давно не обновлялись
    await add_run(sessions, uid, "postings", FAILED, ago=timedelta(minutes=1), error="Ozon временно не отвечает",
                  stats={"error_kind": "ozon_down"})
    async with sessions() as db:  # удачная загрузка 5 минут назад — была не у отправлений
        await db.execute(delete(SyncRun).where(SyncRun.kind == "postings", SyncRun.status == SUCCESS,
                                               SyncRun.started_at > datetime.now(timezone.utc) - H))
        await db.commit()
    await detect(sessions, uid)
    [p] = active(await rows(sessions, uid, det.SYNC_PROBLEM))
    assert (p.key, p.severity, p.details["problem"]) == ("sync:postings", "yellow", "Ozon временно не отвечает")


# ── пропуски начислений ──
def test_accrual_gaps_only_holes_between_loaded_days():
    start = date(2026, 9, 23)
    days = {start + timedelta(days=i): True for i in range(30)}
    for d in (date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 12), date(2026, 10, 21), date(2026, 10, 22)):
        days[d] = False  # 21–22.10 — хвост: отчёт ещё не вышел, не пропуск
    [f] = det.accrual_gaps(days, TH).findings
    assert (f.key, f.severity, f.metric_after) == ("accruals", "yellow", D(3))
    assert f.details["ranges"] == [["2026-10-05", "2026-10-06"], ["2026-10-12", "2026-10-12"]]
    text = describe(det.ACCRUAL_GAPS, "yellow", None, f.metric_after, f.threshold, f.details)
    assert text.title == "Нет начислений за 3 дня" and "05.10–06.10, 12.10" in text.message and "не ноль" in text.message

    assert det.accrual_gaps(days, replace(TH, data_gap_days=4)).findings == []  # порог — настройка
    assert det.accrual_gaps({d: False for d in days}, TH).keep_all  # ничего не загружено — судить не по чему


async def accrual_days(sessions, uid, back: range, skip=()):
    today, now = today_msk(), datetime.now(timezone.utc)
    async with sessions() as db:
        for i in back:
            if i not in skip:
                db.add(AccrualDay(user_id=uid, day=today - timedelta(days=i), synced_at=now, entries=1))
        await db.commit()


async def test_accrual_gaps_detected_and_closed(client, tokens, sessions):
    uid = await me(client, tokens)
    await accrual_days(sessions, uid, range(1, 21), skip=(5, 6))
    await detect(sessions, uid)
    [a] = active(await rows(sessions, uid, det.ACCRUAL_GAPS))
    assert (a.metric_after, a.details["count"]) == (D(2), 2)

    await accrual_days(sessions, uid, range(5, 7))  # дозагрузили
    await detect(sessions, uid)
    assert (await rows(sessions, uid, det.ACCRUAL_GAPS))[0].resolution == "normal"


# ── продажи без себестоимости ──
async def add_sales(sessions, uid, offer: str, qty: int, revenue: str, back=range(1, 4), name: str | None = None):
    today, now = today_msk(), datetime.now(timezone.utc)
    async with sessions() as db:
        if name is not None:
            db.add(Product(user_id=uid, ozon_product_id=abs(hash(offer)) % 10**6, offer_id=offer, name=name, synced_at=now))
        for i in back:
            db.add(SalesDaily(user_id=uid, day=today - timedelta(days=i), offer_id=offer, qty_sold=qty, qty_returned=0,
                              revenue=D(revenue), returns_amount=D(0), source="api", calculated_at=now))
        await db.commit()


async def add_cost(sessions, uid, offer: str, cost: str, back: int = 60):
    async with sessions() as db:
        db.add(ProductCost(user_id=uid, offer_id=offer, cost_price=D(cost), vat_multiplier=D(1),
                           effective_from=today_msk() - timedelta(days=back)))
        await db.commit()


async def test_missing_cost_one_item_updates_and_closes(client, tokens, sessions):
    uid = await me(client, tokens)
    await accrual_days(sessions, uid, range(1, 31), skip=(2,))
    await add_sales(sessions, uid, "A-1", 1, "500", name="Подшипник A")
    await add_sales(sessions, uid, "B-2", 2, "1000", back=range(1, 3))
    await add_sales(sessions, uid, "C-3", 1, "300")
    await add_cost(sessions, uid, "C-3", "100")
    await detect(sessions, uid)
    [a] = active(await rows(sessions, uid, det.MISSING_COST))
    # день 2 — без начислений: продажи за него неизвестны и не считаются. A: дни 1 и 3 × 500, B: день 1 × 1000
    assert (a.severity, a.metric_after, a.details["revenue"]) == ("yellow", D(2), "2000.00")
    assert [x["offer_id"] for x in a.details["top"]] == ["A-1", "B-2"] and a.details["top"][0]["name"] == "Подшипник A"
    text = describe(det.MISSING_COST, "yellow", None, a.metric_after, None, a.details)
    assert text.title == "Продажи без себестоимости: 2 товара" and text.link == "/dashboard/products/costs?missing=1"

    await add_cost(sessions, uid, "A-1", "200")  # заполнили один — тот же пункт, цифры как сейчас
    await detect(sessions, uid)
    [same] = active(await rows(sessions, uid, det.MISSING_COST))
    assert same.id == a.id and same.metric_after == D(1) and same.details["revenue"] == "1000.00"

    await add_cost(sessions, uid, "B-2", "300")
    await detect(sessions, uid)
    assert (await rows(sessions, uid, det.MISSING_COST))[0].resolution == "normal"


# ── отчёт с ошибкой ──
async def test_report_error_until_reread_or_hidden(client, tokens, sessions):
    uid = await me(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        report = Report(user_id=uid, filename="sept.xlsx", report_type="accrual", period="01.09.2026 – 30.09.2026",
                        status=ERROR, error_message="Не найдена таблица начислений.", summary_json={}, file_sha256="0" * 64,
                        file_size=1, uploaded_at=now, parsed_at=now)
        db.add(report)
        await db.commit()
        rid = report.id
    await detect(sessions, uid)
    [a] = active(await rows(sessions, uid, det.REPORT_ERROR))
    assert a.key == f"report:{rid}" and a.severity == "yellow"
    text = describe(det.REPORT_ERROR, "yellow", None, None, None, a.details)
    assert text.title == "Отчёт не перечитался: sept.xlsx" and "Не найдена таблица начислений" in text.message

    async with sessions() as db:
        await db.execute(update(Report).where(Report.id == rid).values(status=READY))
        await db.commit()
    await detect(sessions, uid)
    assert (await rows(sessions, uid, det.REPORT_ERROR))[0].resolution == "normal"


# ── избыток и деньги под риском ──
def test_excess_stock_frozen_money():
    pid, idle, unknown = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    items = [
        det.StockItem(pid, "excess", D("150"), 300, D("2"), cost=D("100")),  # лишнее: 300 − 2 × 60 = 180 шт.
        det.StockItem(idle, "excess", None, 10, D("0"), cost=D("50")),  # не продаётся — лишнее всё
        det.StockItem(unknown, None, None, 10, None),
        det.StockItem(uuid.uuid4(), "normal", D("30"), 60, D("2"), cost=D("100")),
    ]
    check = det.excess_stock(items, TH, sales_known=True)
    assert {f.product_id: f.at_risk for f in check.findings} == {pid: D("18000.00"), idle: D("500.00")}
    assert check.keep == {det.product_key(unknown)}
    assert det.excess_stock(items, TH, sales_known=False).keep_all
    text = describe(det.EXCESS_STOCK, "yellow", None, D("150"), D(60), check.findings[0].details, pid, "Подшипник")
    assert text.title == "Избыток: запаса на 150 дн." and "180 шт. на 18 000 ₽" in text.message
    assert text.link == "/dashboard/warehouses/withdrawal"
    idle_text = describe(det.EXCESS_STOCK, "yellow", None, None, D(60), check.findings[1].details, idle, "Втулка")
    assert idle_text.title == "Лежит без продаж: 10 шт."


def test_money_at_risk_where_it_can_be_estimated():
    pid, promo = uuid.uuid4(), uuid.uuid4()
    today = date(2026, 10, 23)
    ends = datetime(2026, 10, 27, 20, tzinfo=timezone.utc)  # ещё 5 дней с сегодняшним
    [loss] = det.promo_loss([det.PromoItem(pid, promo, "Распродажа", D("1000"), False, D("-10"), ends, D("2"))], today).findings
    assert loss.at_risk == D("1000.00")  # 10 % × 1000 ₽ × 2 шт. в день × 5 дней
    no_sales = det.promo_loss([det.PromoItem(pid, promo, "Распродажа", D("1000"), False, D("-10"), ends, D("0"))], today)
    assert no_sales.findings[0].at_risk is None  # не продаётся — оценить нельзя

    margins = [det.MarginItem(pid, D("-5"), D("1000"), D("1")), det.MarginItem(promo, D("5"), D("1000"), D("1"))]
    assert [f.at_risk for f in det.low_margin(margins, TH).findings] == [D("1500.00"), None]  # убыток за 30 дней

    [missed] = det.payout_missed([today], set(), {today: D("12000")}).findings
    assert missed.at_risk == D("12000.00")
    facts = [det.PayoutFact(today, D("8500"), D("10000")), det.PayoutFact(today - timedelta(days=7), D("12000"), D("10000"))]
    assert [f.at_risk for f in det.payout_diff(facts, today, TH).findings] == [D("1500.00"), None]  # больше — не риск

    days = [det.CashDay(today + timedelta(days=i), b) for i, b in enumerate([D(5000), D(-3000), D(2000)])]
    assert det.cash_gap(days, today, TH, D(10000), True, True).findings[0].at_risk == D("3000.00")
    above = [det.CashDay(today, D(8000))]
    assert det.cash_gap(above, today, TH, D(10000), True, True).findings[0].at_risk == D("2000.00")  # до порога


def test_reconcile_refreshes_money_and_live_items():
    costs = det.Active(uuid.uuid4(), det.MISSING_COST, "costs", "yellow")
    excess = det.Active(uuid.uuid4(), det.EXCESS_STOCK, "product:x", "yellow")
    f_costs = det.Finding(det.MISSING_COST, "costs", "yellow", metric_after=D(1), details={"count": 1})
    f_excess = det.Finding(det.EXCESS_STOCK, "product:x", "yellow", at_risk=D(700))
    plan = det.reconcile([costs, excess], [det.Check(det.MISSING_COST, [f_costs], live=True),
                                           det.Check(det.EXCESS_STOCK, [f_excess])])
    assert plan.create == [] and plan.resolve == {}
    assert plan.refresh == {costs.id: (f_costs, True), excess.id: (f_excess, False)}


async def test_excess_detected_with_money_at_risk(client, tokens, sessions):
    uid = await me(client, tokens)
    await accrual_days(sessions, uid, range(1, 31))
    await add_sales(sessions, uid, "E-1", 2, "800", back=range(1, 31), name="Подшипник E")
    await add_cost(sessions, uid, "E-1", "100")
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        pid = await db.scalar(select(Product.id).where(Product.offer_id == "E-1"))
        w = Warehouse(user_id=uid, ozon_warehouse_id=5, name="Склад", type="FBO", synced_at=now)
        db.add(w)
        await db.flush()
        db.add(ProductStock(product_id=pid, warehouse_id=w.id, snapshot_at=now, present=300))
        await db.commit()
    await detect(sessions, uid)
    [a] = active(await rows(sessions, uid, det.EXCESS_STOCK))
    assert (a.product_id, a.money_at_risk, a.details["excess_qty"]) == (pid, D("18000.00"), 180)


# ── порядок и API ──
async def test_attention_order_severity_money_then_new(client, tokens, sessions):
    uid = await me(client, tokens)
    now = datetime.now(timezone.utc)
    specs = [  # (ключ, важность, деньги, сколько часов назад)
        ("y-new", "yellow", None, 1), ("r-old-100", "red", "100", 30), ("r-new", "red", None, 1),
        ("r-5000", "red", "5000", 10), ("y-300", "yellow", "300", 20),
    ]
    async with sessions() as db:
        for key, severity, money, hours in specs:
            at = now - timedelta(hours=hours)
            db.add(Anomaly(user_id=uid, type=det.SYNC_PROBLEM, key=key, severity=severity, detected_at=at, last_seen_at=at,
                           money_at_risk=D(money) if money else None, details={"titles": ["Остатки"]}))
        await db.commit()
    r = await client.get("/api/v1/anomalies", params={"acknowledged": "false", "limit": 7}, headers=auth(tokens))
    body = r.json()
    order = [(i["severity"], i["money_at_risk"]) for i in body["items"]]
    assert order == [("red", "5000.00"), ("red", "100.00"), ("red", None), ("yellow", "300.00"), ("yellow", None)]
    assert body["items"][0]["group"] == "data" and body["by_group"] == {"data": 5}
    assert (await client.get("/api/v1/anomalies", params={"group": "data"}, headers=auth(tokens))).json()["total"] == 5


# ── Telegram и настройки ──
async def test_data_category_off_in_telegram_by_default(client, tokens):
    on = notify.Prefs(notify.legacy_sections(data=True))
    for t in (det.SYNC_PROBLEM, det.ACCRUAL_GAPS, det.REPORT_ERROR, det.MISSING_COST):
        assert notify.section_of(t) == "data" and not notify.wanted(notify.Prefs(), t, "red")
        assert notify.wanted(on, t, "yellow")
    assert notify.section_of(det.EXCESS_STOCK) == "stock"  # избыток — «Остатки»
    assert "синхронизация и отчёты" in notify.render_test("https://x", on)

    data = lambda body: next(x for x in body["sections"] if x["key"] == "data")  # noqa: E731
    get = await client.get("/api/v1/notifications/settings", headers=auth(tokens))
    assert data(get.json())["telegram"] is False
    r = await client.patch("/api/v1/notifications/settings", json={"sections": {"data": {"telegram": True}}},
                           headers=auth(tokens))
    assert r.status_code == 200 and data(r.json())["telegram"] is True


async def test_data_thresholds_are_settings(client, tokens):
    r = await client.patch("/api/v1/settings", json={"anomaly_sync_stale_hours": 48, "anomaly_data_gap_days": 3},
                           headers=auth(tokens))
    assert r.status_code == 200
    assert (r.json()["anomaly_sync_stale_hours"], r.json()["anomaly_data_gap_days"], r.json()["anomaly_defaults"]) == (48, 3, False)
    bad = await client.patch("/api/v1/settings", json={"anomaly_data_gap_days": 0}, headers=auth(tokens))
    assert bad.status_code == 400
