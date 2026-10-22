"""Детектор аномалий (шаг 7.1): каждый детектор, сверка с активными, тексты. Чистый модуль — без БД."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.engine import anomaly_detector as det
from app.engine.anomaly_detector import (
    RED,
    YELLOW,
    Active,
    BankRow,
    CashDay,
    MarginItem,
    OpItem,
    PayoutFact,
    Point,
    PromoItem,
    SalesData,
    StockItem,
    Thresholds,
)
from app.engine.anomaly_text import describe
from app.engine.workdays import is_workday

TH = Thresholds()
TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
P1, P2 = uuid.UUID(int=1), uuid.UUID(int=2)


def days_back(n: int) -> list[date]:
    """n дней до сегодня (сегодня не входит), по порядку."""
    return [TODAY - timedelta(days=i) for i in range(n, 0, -1)]


def series(base: int, recent: int, n: int = 40) -> dict[date, int]:
    """Продажи по дням: последние 3 дня — recent в день, раньше — base в день."""
    return {d: (recent if d >= TODAY - timedelta(days=3) else base) for d in days_back(n)}


def data(shop: dict[date, int], products: dict | None = None, covered: set[date] | None = None, **kw) -> SalesData:
    return SalesData(TODAY, covered if covered is not None else set(days_back(40)), shop, products or {}, **kw)


# ── продажи ──
def test_sales_drop_yellow_and_red():
    check = det.sales_drop(data(series(10, 5)), TH)
    [f] = check.findings
    assert (f.type, f.key, f.severity, f.threshold) == (det.SALES_DROP, "shop", YELLOW, 40)
    assert f.metric_before == Decimal("10.000") and f.metric_after == Decimal("5.000")  # в день: ожидали → продали
    assert f.details["actual"] == 15 and f.details["expected"] == 30 and f.details["change_pct"] == -50

    [f] = det.sales_drop(data(series(10, 2)), TH).findings
    assert f.severity == RED and f.threshold == 70


def test_sales_drop_by_product_and_random_noise():
    """У товара с 1 продажей в день три дня без продаж — не аномалия (столько бывает случайно); у товара с 5 в день — да."""
    products = {P1: series(1, 0), P2: series(5, 0)}
    check = det.sales_drop(data(series(6, 6), products), TH)
    assert [(f.key, f.severity) for f in check.findings] == [(det.product_key(P2), RED)]
    assert check.findings[0].product_id == P2


def test_sales_drop_zero_stock_is_not_judged():
    check = det.sales_drop(data(series(5, 5), {P1: series(5, 0)}, zero_stock={P1}), TH)
    assert check.findings == [] and det.product_key(P1) in check.keep


def test_sales_days_without_data_are_not_zero_sales():
    """Дни без данных — не «0 продаж»: ни в последних трёх днях, ни в семи перед ними."""
    shop = series(10, 10)
    covered = set(days_back(40)) - {TODAY - timedelta(days=1)} - {TODAY - timedelta(days=d) for d in (5, 6)}
    c = det.compare(shop, data(shop, covered=covered), {True: 1.0, False: 1.0})
    assert (c.days, c.base_days, c.actual, c.expected) == (2, 5, 20, 20.0)
    assert det.sales_drop(data(shop, covered=covered), TH).findings == []
    # из последних трёх дней данные только за один — не судим, активную не закрываем
    covered = set(days_back(40)) - {TODAY - timedelta(days=1), TODAY - timedelta(days=2)}
    assert "shop" in det.sales_drop(data(shop, covered=covered), TH).keep


def test_sales_weekends_are_taken_into_account():
    """В выходные продаж меньше: три последних дня — пятница и выходные — ниже среднего, но это не падение."""
    monday = date(2026, 10, 19)
    assert monday.weekday() == 0
    shop = {d: (20 if is_workday(d) else 2) for d in (monday - timedelta(days=i) for i in range(1, 41))}
    d = SalesData(monday, set(shop), shop, {})
    factors = det.weekday_factors(d)
    assert factors[True] > 1 > factors[False]
    assert det.sales_drop(d, TH).findings == []
    # без поправки было бы «−46 %»
    flat = det.compare(shop, d, {True: 1.0, False: 1.0})
    assert flat.change_pct < -40


def test_sales_drop_hysteresis():
    """Активная не закрывается, пока падение больше половины порога, — без «мигания» вокруг 40 %."""
    assert "shop" in det.sales_drop(data(series(10, 7)), TH, active={"shop"}).keep  # −30 %
    assert "shop" not in det.sales_drop(data(series(10, 9)), TH, active={"shop"}).keep  # −10 % — норма
    assert det.sales_drop(data(series(10, 7)), TH).keep == set()


def test_sales_growth():
    [f] = det.sales_growth(data(series(2, 10)), TH).findings
    assert (f.type, f.severity, f.threshold, f.details["change_pct"]) == (det.SALES_GROWTH, YELLOW, 200, 400)
    # раньше не продавался — роста «в процентах» нет
    assert det.sales_growth(data(series(10, 10), {P1: series(0, 3)}), TH).findings == []
    assert det.sales_growth(data(series(10, 25)), TH).findings == []  # +150 % — меньше порога


def test_sales_thresholds_are_user_settings():
    th = Thresholds(sales_drop=20, sales_drop_red=60)
    [f] = det.sales_drop(data(series(100, 70)), th).findings  # −30 %
    assert (f.severity, f.threshold) == (YELLOW, 20)


def test_poisson():
    assert abs(det.poisson_le(0, 3) - 0.0498) < 1e-3
    assert det.poisson_le(10, 0) == 1.0
    assert det.poisson_ge(0, 5) == 1.0
    assert det.poisson_le(500, 1000) < 1e-30  # большие числа — без переполнения


# ── цена и комиссия ──
def pts(*items: tuple[int, str | None]) -> list[Point]:
    """(дней назад, значение)."""
    return [Point(NOW - timedelta(days=d), Decimal(v) if v is not None else None) for d, v in items]


def test_price_change():
    check = det.price_change({P1: pts((10, "1000"), (2, "1150")), P2: pts((10, "1000"), (1, "1400"))}, NOW, TH)
    found = {f.product_id: f for f in check.findings}
    assert (found[P1].severity, found[P1].metric_before, found[P1].metric_after) == (YELLOW, Decimal(1000), Decimal(1150))
    assert found[P1].details["change_pct"] == 15.0 and found[P1].threshold == 10
    assert (found[P2].severity, found[P2].threshold) == (RED, 30)
    assert found[P2].details["via_service"] is False


def test_price_change_window_and_return():
    # снимков 7 дней назад ещё нет — с первым снимком
    assert det.price_change({P1: pts((3, "1000"), (1, "1050"))}, NOW, TH).findings == []
    assert det.price_change({P1: pts((3, "1000"), (1, "1150"))}, NOW, TH).findings[0].severity == YELLOW
    # цена вернулась
    assert det.price_change({P1: pts((10, "1000"), (3, "1200"), (1, "1000"))}, NOW, TH).findings == []
    # изменение старше 7 дней — новая цена стала обычной
    assert det.price_change({P1: pts((20, "1000"), (10, "1200"))}, NOW, TH).findings == []
    # цены нет или 0 у Ozon — не судим
    assert det.product_key(P1) in det.price_change({P1: pts((10, "1000"), (1, "0"))}, NOW, TH).keep


def test_price_change_via_service_is_anomaly_too():
    """Решение владельца 17.10.2026: изменение через сервис — тоже аномалия, с пометкой."""
    check = det.price_change({P1: pts((10, "1000"), (1, "1200"))}, NOW, TH, {P1: [NOW - timedelta(days=1)]})
    [f] = check.findings
    assert f.details["via_service"] is True
    old = det.price_change({P1: pts((10, "1000"), (1, "1200"))}, NOW, TH, {P1: [NOW - timedelta(days=9)]})
    assert old.findings[0].details["via_service"] is False


def test_commission_change():
    check = det.commission_change({P1: pts((10, "15"), (1, "17.5")), P2: pts((10, "15"), (1, "16.5"))}, NOW, TH)
    [f] = check.findings
    assert (f.product_id, f.severity, f.threshold, f.details["change_pp"]) == (P1, YELLOW, 2, 2.5)
    assert det.commission_change({P1: pts((10, "15"), (1, "13"))}, NOW, Thresholds(commission_pp=Decimal("1.5"))).findings


# ── маржа, остатки, акции ──
def test_low_margin():
    items = [MarginItem(P1, Decimal("5"), Decimal("1000")), MarginItem(P2, Decimal("12"), Decimal("900")),
             MarginItem(uuid.UUID(int=3), None, None)]
    check = det.low_margin(items, TH)
    [f] = check.findings
    assert (f.product_id, f.severity, f.metric_after, f.threshold) == (P1, RED, Decimal(5), Decimal(10))
    assert check.keep == {det.product_key(uuid.UUID(int=3))}
    assert len(det.low_margin(items, Thresholds(low_margin=Decimal(15))).findings) == 2  # порог — настройка


def test_low_stock():
    items = [StockItem(P1, "critical", Decimal("3.5"), 7, Decimal(2)), StockItem(P2, "low", Decimal("10"), 20, Decimal(2)),
             StockItem(uuid.UUID(int=3), "normal", Decimal("30"), 60, Decimal(2))]
    found = {f.product_id: f for f in det.low_stock(items, TH, sales_known=True).findings}
    assert (found[P1].severity, found[P1].threshold, found[P1].metric_after) == (RED, 7, Decimal("3.5"))
    assert (found[P2].severity, found[P2].threshold) == (YELLOW, 14)
    assert len(found) == 2
    # продажи неизвестны — дней запаса нет: не «остаток 0», активные не закрываем
    unknown = det.low_stock(items, TH, sales_known=False)
    assert unknown.findings == [] and unknown.keep_all


PROMO = uuid.UUID(int=10)


def test_promo_auto_added():
    items = [PromoItem(P1, PROMO, "Распродажа", Decimal(900), True, Decimal(8)),
             PromoItem(P2, PROMO, "Распродажа", Decimal(900), False, Decimal(8))]
    [f] = det.promo_auto_added(items).findings
    assert (f.key, f.severity, f.details["promotion"]) == (det.promo_key(P1, PROMO), YELLOW, "Распродажа")


def test_promo_loss():
    items = [PromoItem(P1, PROMO, "Распродажа", Decimal(500), False, Decimal(-3)),
             PromoItem(P2, PROMO, "Распродажа", Decimal(900), True, Decimal(5)),
             PromoItem(uuid.UUID(int=3), PROMO, "Распродажа", Decimal(900), True, None)]
    check = det.promo_loss(items)
    [f] = check.findings
    assert (f.product_id, f.severity, f.metric_after) == (P1, RED, Decimal(-3))
    assert check.keep == {det.promo_key(uuid.UUID(int=3), PROMO)}


# ── выплаты Ozon ──
def test_payout_missed():
    late = date(2026, 10, 7)
    check = det.payout_missed([late], {date(2026, 9, 30)}, {late: Decimal(15000)})
    [f] = check.findings
    assert (f.key, f.severity, f.metric_before) == ("payout:2026-10-07", RED, Decimal(15000))
    old = "payout:2026-09-23"
    # просрочка старше двух недель, выплаты после неё так и нет — не закрываем
    assert old in det.payout_missed([], {date(2026, 9, 16)}, {}, active={old}).keep
    # после ожидаемого дня выплата пришла — закрывается
    assert old not in det.payout_missed([], {date(2026, 9, 25)}, {}, active={old}).keep


def test_payout_diff():
    facts = [PayoutFact(date(2026, 10, 14), Decimal(8500), Decimal(10000), date(2026, 9, 21)),
             PayoutFact(date(2026, 10, 7), Decimal(9500), Decimal(10000)),
             PayoutFact(date(2026, 9, 23), Decimal(5000), Decimal(10000)),  # старше 14 дней
             PayoutFact(date(2026, 9, 30), Decimal(5000), None)]
    [f] = det.payout_diff(facts, TODAY, TH).findings
    assert (f.key, f.severity, f.metric_before, f.metric_after, f.details["change_pct"]) == (
        "payout:2026-10-14", YELLOW, Decimal(10000), Decimal(8500), -15.0)
    unreliable = det.payout_diff(facts, TODAY, TH, reliable=False)
    assert unreliable.findings == [] and unreliable.keep_all


def test_bank_checks():
    rows = [BankRow(date(2026, 10, 7), "no_bank", Decimal(15000)),
            BankRow(date(2026, 10, 14), "waiting", Decimal(12000)),
            BankRow(date(2026, 9, 30), "diff", Decimal(10000), Decimal(9950), date(2026, 10, 1), Decimal(-50)),
            BankRow(date(2026, 9, 23), "diff", Decimal(10000), Decimal("9999.5"), date(2026, 9, 24), Decimal("-0.5")),
            BankRow(date(2026, 9, 16), "ok", Decimal(10000), Decimal(10000), date(2026, 9, 17), Decimal(0))]
    missing = det.bank_missing(rows, enabled=True)
    assert [(f.key, f.severity) for f in missing.findings] == [("payout:2026-10-07", RED)]
    assert missing.keep == {"payout:2026-10-14"}
    diff = det.bank_diff(rows, enabled=True)
    assert [(f.key, f.metric_after) for f in diff.findings] == [("payout:2026-09-30", Decimal(9950))]
    assert det.bank_missing(rows, enabled=False).off and det.bank_missing(rows, enabled=False).findings == []


# ── планирование ──
def cash(*balances: int) -> list[CashDay]:
    return [CashDay(TODAY + timedelta(days=i), Decimal(b)) for i, b in enumerate(balances)]


def test_cash_gap():
    [f] = det.cash_gap(cash(5000, 3000, -2000, -500, 4000), TODAY, TH, None, True, True).findings
    assert (f.severity, f.metric_after, f.details["first_day"]) == (RED, Decimal(-2000), "2026-10-17")
    [f] = det.cash_gap(cash(5000, 3000, 800, 4000), TODAY, TH, Decimal(1000), True, True).findings
    assert (f.severity, f.threshold, f.details["first_day"]) == (YELLOW, Decimal(1000), "2026-10-17")
    # разрыв дальше 14 дней — не наше окно
    assert det.cash_gap(cash(*([5000] * 20 + [-1])), TODAY, TH, None, True, True).findings == []
    assert det.cash_gap(cash(*([5000] * 20 + [-1])), TODAY, Thresholds(cash_gap_days=30), None, True, True).findings
    assert det.cash_gap(cash(-1), TODAY, TH, None, enabled=False, start_known=True).off
    unknown = det.cash_gap([], TODAY, TH, None, enabled=True, start_known=False)
    assert unknown.keep_all and not unknown.off


def test_uncategorized():
    ops = [OpItem(uuid.uuid4(), NOW - timedelta(days=10), date(2026, 9, 1), Decimal(-1500)),
           OpItem(uuid.uuid4(), NOW - timedelta(days=8), date(2026, 9, 20), Decimal(3000)),
           OpItem(uuid.uuid4(), NOW - timedelta(days=2), date(2026, 10, 12), Decimal(-100))]
    [f] = det.uncategorized(ops, NOW, TH, enabled=True).findings
    assert (f.severity, f.metric_after, f.details["oldest_day"], f.details["expense"]) == (YELLOW, Decimal(2), "2026-09-01", "-1500")
    assert det.uncategorized(ops[2:], NOW, TH, enabled=True).findings == []
    assert det.uncategorized(ops, NOW, TH, enabled=False).off


# ── сверка с активными ──
def finding(key: str, severity: str = YELLOW, type_: str = det.SALES_DROP, pid=None) -> det.Finding:
    return det.Finding(type_, key, severity, pid)


def active(key: str, severity: str = YELLOW, type_: str = det.SALES_DROP, pid=None) -> Active:
    return Active(uuid.uuid4(), type_, key, severity, pid)


def test_reconcile_create_dedupe_resolve():
    a_same, a_gone = active("shop"), active("product:x")
    plan = det.reconcile([a_same, a_gone], [det.Check(det.SALES_DROP, [finding("shop"), finding("product:y")])])
    assert [f.key for f in plan.create] == ["product:y"]  # активная того же типа и предмета — не дубль
    assert plan.seen == [a_same.id]
    assert plan.resolve == {a_gone.id: det.NORMAL}


def test_reconcile_escalation_and_no_downgrade():
    yellow, red = active("shop"), active("product:x", RED)
    plan = det.reconcile([yellow, red], [det.Check(det.SALES_DROP, [finding("shop", RED), finding("product:x", YELLOW)])])
    assert plan.resolve == {yellow.id: det.ESCALATED}
    assert [(f.key, f.severity) for f in plan.create] == [("shop", RED)]
    assert plan.seen == [red.id] and plan.escalated == 1


def test_reconcile_keep_off_gone_and_untouched_types():
    kept, kept_all, off = active("shop"), active("product:1", type_=det.LOW_STOCK), active("cash", type_=det.CASH_GAP)
    archived = active(det.product_key(P1), type_=det.LOW_MARGIN, pid=P1)
    untouched = active("payout:2026-10-07", RED, det.PAYOUT_MISSED)
    plan = det.reconcile(
        [kept, kept_all, off, archived, untouched],
        [det.Check(det.SALES_DROP, keep={"shop"}), det.Check(det.LOW_STOCK, keep_all=True),
         det.Check(det.CASH_GAP, off=True), det.Check(det.LOW_MARGIN)],
        gone_products={P1},
    )
    assert plan.resolve == {off.id: det.GONE, archived.id: det.GONE}
    assert plan.create == [] and plan.seen == []


def test_thresholds_validation():
    assert Thresholds().error() is None
    assert "жёлтый порог меньше красного" in Thresholds(sales_drop=70, sales_drop_red=70).error()
    assert "Изменение цены" in Thresholds(price=30, price_red=20).error()


# ── тексты ──
def test_texts():
    t = describe(det.SALES_DROP, YELLOW, Decimal(10), Decimal(5), Decimal(40),
                 {"change_pct": -50.0, "actual": 15, "expected": 30.0, "days": 3})
    assert t.title == "Продажи магазина упали на 50 %"
    assert "продано 15 шт." in t.message and "около 30 шт." in t.message
    t = describe(det.PRICE_CHANGE, YELLOW, Decimal(1000), Decimal(1150), Decimal(10),
                 {"change_pct": 15.0, "via_service": False}, P1, "Подшипник 6001")
    assert t.title == "Цена выросла на 15 %: 1 000 ₽ → 1 150 ₽"
    assert "кабинете Ozon" in t.message and t.link == f"/dashboard/products?product={P1}"
    t = describe(det.PRICE_CHANGE, RED, Decimal(1000), Decimal(600), Decimal(30), {"change_pct": -40.0, "via_service": True}, P1)
    assert t.title.startswith("Цена снизилась на 40 %") and t.link == f"/dashboard/products/prices/history?product={P1}"
    t = describe(det.LOW_MARGIN, RED, None, Decimal("-2.5"), Decimal(10), {"price": "990"}, P1, "6001")
    assert t.title == "Маржа −2,5 % — каждая продажа в минус"
    t = describe(det.PAYOUT_MISSED, RED, Decimal("15000.5"), None, None, {"day": "2026-10-07"})
    assert t.title == "Выплата Ozon 07.10 не пришла" and "около 15 000,50 ₽" in t.message
    t = describe(det.CASH_GAP, RED, None, Decimal(-2000), Decimal(0), {"first_day": "2026-10-17", "min_day": "2026-10-17"})
    assert t.title == "Кассовый разрыв 17.10" and t.link == "/dashboard/planning/calendar"
    t = describe(det.UNCATEGORIZED, YELLOW, None, Decimal(2), Decimal(7), {"oldest_day": "2026-09-01"}, today=TODAY)
    assert t.title == "Операций без статьи: 2"
    assert t.link == "/dashboard/operations?category=none&from=2026-09-01&to=2026-10-15"
    t = describe(det.LOW_STOCK, RED, None, Decimal(0), Decimal(7), {"available": 0, "avg_daily": "1.5"}, P1)
    assert t.title == "Товар закончился"
