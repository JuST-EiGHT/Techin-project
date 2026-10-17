"""Аномалии (шаг 7.1): детекторы и сверка с активными аномалиями. Чистый модуль — без БД и HTTP.

Каждый детектор — отдельная функция: на входе данные (уже собранные сервисом services/anomalies) и пороги пользователя,
на выходе `Check` — что сейчас аномально (`Finding`) и о чём судить нельзя (`keep`: нет данных, «нет данных ≠ норма»).
`reconcile` сверяет проверки с активными аномалиями: новая — создаётся (не дубль: активная того же типа и предмета —
одна), прошла — закрывается (resolved_at), жёлтая стала красной — жёлтая закрывается, создаётся красная; где данных нет —
ничего не трогаем. Пороги по умолчанию — BUSINESS_LOGIC §7, их меняет пользователь («Основные настройки → Аномалии»).

Как считаем (подробно — BUSINESS_LOGIC §7, «Как сделано в сервисе»):
- продажи: последние 3 полных дня против 7 дней перед ними, только дни с данными; ожидание — с поправкой на выходные
  и праздники (доля рабочих и нерабочих дней по продажам магазина за 4 недели); у малых продаж падение должно быть
  заметно на фоне случайных колебаний (распределение Пуассона, p < 1 %); закрывается, когда изменение меньше половины
  порога — без «мигания» вокруг порога;
- цена и комиссия: сейчас против 7 дней назад (до первого снимка — с первым снимком); через 7 дней после изменения
  новая цена становится обычной — аномалия закрывается; вернулась раньше — закрывается сразу;
- маржа, дни запаса, акции, кассовый разрыв, операции без статьи — состояние: пока условие выполняется;
- выплаты Ozon: не пришла — пока не придёт любая выплата после ожидаемого дня; разошлась с прогнозом — 14 дней.
"""
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.engine.workdays import is_workday

YELLOW, RED = "yellow", "red"
_RANK = {YELLOW: 1, RED: 2}
NORMAL, ESCALATED, GONE = "normal", "escalated", "gone"  # почему закрыта — models/anomaly

# ── типы аномалий ──
SALES_DROP = "sales_drop"
SALES_GROWTH = "sales_growth"
PRICE_CHANGE = "price_change"
COMMISSION_CHANGE = "commission_change"
LOW_MARGIN = "low_margin"
LOW_STOCK = "low_stock"
PROMO_AUTO_ADDED = "promo_auto_added"
PROMO_LOSS = "promo_loss"
PAYOUT_MISSED = "payout_missed"  # выплата Ozon не пришла в ожидаемый день
PAYOUT_DIFF = "payout_diff"  # выплата разошлась с прогнозом
BANK_MISSING = "bank_missing"  # выплата не дошла до счёта (выписка за эти дни есть)
BANK_DIFF = "bank_diff"  # на счёт пришло не столько, сколько выплатил Ozon
CASH_GAP = "cash_gap"  # прогноз кассового разрыва (планирование)
UNCATEGORIZED = "uncategorized"  # операции без статьи (планирование)

# группа — для фильтров и настроек уведомлений (7.2, 7.3): товары, акции, деньги
GROUPS = {
    SALES_DROP: "sales", SALES_GROWTH: "sales", PRICE_CHANGE: "price", COMMISSION_CHANGE: "price",
    LOW_MARGIN: "margin", LOW_STOCK: "stock", PROMO_AUTO_ADDED: "promo", PROMO_LOSS: "promo",
    PAYOUT_MISSED: "money", PAYOUT_DIFF: "money", BANK_MISSING: "money", BANK_DIFF: "money",
    CASH_GAP: "money", UNCATEGORIZED: "money",
}
TYPES = tuple(GROUPS)

SHOP = "shop"
RECENT_DAYS, BASE_DAYS, FACTOR_DAYS = 3, 7, 28  # 3 дня против 7 предыдущих; выходные — по 4 неделям
MIN_RECENT_DAYS, MIN_BASE_DAYS, MIN_BUCKET_DAYS = 2, 5, 3  # меньше дней с данными — не судим
SIGNIFICANCE = 0.01  # падение или рост не объясняются случайными колебаниями продаж
CHANGE_WINDOW = timedelta(days=7)  # цена и комиссия: с чем сравниваем
PAYOUT_DIFF_DAYS = 14  # выплаты, расхождение которых показываем
BANK_DIFF_MIN = Decimal(1)  # копейки округления — не расхождение
_ROUND = Decimal("0.001")


@dataclass(frozen=True)
class Thresholds:
    """Пороги пользователя (BUSINESS_LOGIC §7). Дни запаса — пороги остатков шага 5.1: меньше «Мало» — жёлтая, меньше
    «Критично» — красная."""

    sales_drop: int = 40  # %, жёлтая
    sales_drop_red: int = 70
    sales_growth: int = 200
    price: int = 10  # %, жёлтая
    price_red: int = 30
    commission_pp: Decimal = Decimal(2)
    low_margin: Decimal = Decimal(10)  # %, общий порог «низкой маржи»: акции, товары, прибыль по товарам
    stock_critical: int = 7
    stock_low: int = 14
    payout_diff: int = 10  # %
    cash_gap_days: int = 14
    uncategorized_days: int = 7

    def error(self) -> str | None:
        """Текст для пользователя, если пороги не подходят; иначе None."""
        if not 1 <= self.sales_drop < self.sales_drop_red <= 99:
            return "Падение продаж: жёлтый порог меньше красного, оба — от 1 до 99 %"
        if not self.price < self.price_red:
            return "Изменение цены: жёлтый порог меньше красного"
        return None


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class Finding:
    """Что аномально сейчас. key — предмет внутри типа: shop, product:<id>, product:<id>:promo:<id>, payout:<дата> …"""

    type: str
    key: str
    severity: str
    product_id: uuid.UUID | None = None
    metric_before: Decimal | None = None
    metric_after: Decimal | None = None
    threshold: Decimal | None = None
    details: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass
class Check:
    """Итог одного детектора. keep — предметы, о которых судить нельзя (нет данных) или которые ещё не вернулись в норму
    с запасом: активные по ним не закрываются; keep_all — то же для всех; off — проверка не относится к пользователю
    (планирование выключено, банка нет): активные закрываются как gone."""

    type: str
    findings: list[Finding] = field(default_factory=list)
    keep: set[str] = field(default_factory=set)
    keep_all: bool = False
    off: bool = False


def product_key(product_id: uuid.UUID) -> str:
    return f"product:{product_id}"


def promo_key(product_id: uuid.UUID, promotion_id: uuid.UUID) -> str:
    return f"product:{product_id}:promo:{promotion_id}"


def day_key(prefix: str, day: date) -> str:
    return f"{prefix}:{day.isoformat()}"


def _d(v: float | Decimal | int) -> Decimal:
    return Decimal(str(v)).quantize(_ROUND, ROUND_HALF_UP)


# ── продажи ──
@dataclass
class SalesData:
    """Продажи по дням (sales_daily, шаг 3.5). covered — дни, за которые данные есть: в остальные продажи неизвестны (не 0).
    В днях с данными товара без записи — 0 продаж."""

    today: date
    covered: set[date]
    shop: dict[date, int]  # продано штук за день по всему магазину
    products: dict[uuid.UUID, dict[date, int]]  # по товару (в продаже, не в архиве)
    zero_stock: set[uuid.UUID] = field(default_factory=set)  # остаток известен и равен 0 — падение объясняет он


@dataclass
class Comparison:
    expected: float  # сколько ожидали продать за дни с данными из последних 3 (по 7 предыдущим, с поправкой на выходные)
    actual: int
    days: int  # дней с данными из последних 3
    base_days: int  # из 7 предыдущих
    base_avg: float  # в среднем за день в 7 предыдущих

    @property
    def change_pct(self) -> float | None:
        return None if self.expected <= 0 else (self.actual / self.expected - 1) * 100


def _days(today: date, start_back: int, length: int) -> list[date]:
    """length дней, первый — today − start_back."""
    return [today - timedelta(days=start_back - i) for i in range(length)]


def weekday_factors(data: SalesData) -> dict[bool, float]:
    """Во сколько раз в рабочий и в нерабочий день (выходные и праздники) продают больше среднего — по продажам магазина
    за 4 недели перед последними тремя днями. Данных мало — 1 и 1 (поправки нет)."""
    days = [d for d in _days(data.today, RECENT_DAYS + FACTOR_DAYS, FACTOR_DAYS) if d in data.covered]
    buckets: dict[bool, list[int]] = {True: [], False: []}
    for d in days:
        buckets[is_workday(d)].append(data.shop.get(d, 0))
    total = sum(sum(v) for v in buckets.values())
    if total <= 0 or any(len(v) < MIN_BUCKET_DAYS for v in buckets.values()):
        return {True: 1.0, False: 1.0}
    mean = total / len(days)
    return {k: min(max(sum(v) / len(v) / mean, 0.1), 10.0) for k, v in buckets.items()}


def compare(series: dict[date, int], data: SalesData, factors: dict[bool, float]) -> Comparison | None:
    """Последние 3 полных дня против 7 перед ними — только дни с данными. Мало дней с данными — None (не судим)."""
    recent = [d for d in _days(data.today, RECENT_DAYS, RECENT_DAYS) if d in data.covered]
    base = [d for d in _days(data.today, RECENT_DAYS + BASE_DAYS, BASE_DAYS) if d in data.covered]
    if len(recent) < MIN_RECENT_DAYS or len(base) < MIN_BASE_DAYS:
        return None
    base_sold = sum(series.get(d, 0) for d in base)
    rate = base_sold / sum(factors[is_workday(d)] for d in base)
    expected = rate * sum(factors[is_workday(d)] for d in recent)
    return Comparison(expected, sum(series.get(d, 0) for d in recent), len(recent), len(base), base_sold / len(base))


def _log_pmf(i: int, lam: float) -> float:
    return -lam + i * math.log(lam) - math.lgamma(i + 1)


def poisson_le(k: int, lam: float) -> float:
    """P(X ≤ k) для X ~ Пуассон(lam)."""
    if lam <= 0:
        return 1.0
    if k < 0:
        return 0.0
    logs = [_log_pmf(i, lam) for i in range(k + 1)]
    top = max(logs)
    return min(1.0, math.exp(top) * sum(math.exp(x - top) for x in logs))


def poisson_ge(k: int, lam: float) -> float:
    """P(X ≥ k)."""
    return 1.0 if k <= 0 else max(0.0, 1.0 - poisson_le(k - 1, lam))


def _subjects(data: SalesData) -> list[tuple[str, uuid.UUID | None, dict[date, int]]]:
    return [(SHOP, None, data.shop)] + [(product_key(pid), pid, s) for pid, s in data.products.items()]


def _sales_finding(kind: str, key: str, pid: uuid.UUID | None, c: Comparison, severity: str, threshold: int) -> Finding:
    return Finding(
        kind, key, severity, pid,
        metric_before=_d(c.expected / c.days), metric_after=_d(c.actual / c.days), threshold=Decimal(threshold),
        details={"expected": round(c.expected, 1), "actual": c.actual, "days": c.days, "base_days": c.base_days,
                 "base_avg": round(c.base_avg, 2), "change_pct": round(c.change_pct, 1)},
    )


def sales_drop(data: SalesData, th: Thresholds, active: set[str] = frozenset()) -> Check:
    """Падение продаж: последние 3 дня продали меньше ожидаемого больше чем на порог (жёлтая 40 %, красная 70 %), и это не
    случайность. Товар с нулевым остатком не судим — его покажет «Дни запаса»."""
    check = Check(SALES_DROP)
    factors = weekday_factors(data)
    for key, pid, series in _subjects(data):
        if pid is not None and pid in data.zero_stock:
            check.keep.add(key)
            continue
        c = compare(series, data, factors)
        if c is None:
            check.keep.add(key)
            continue
        change = c.change_pct
        if change is None:
            continue
        if -change > th.sales_drop and poisson_le(c.actual, c.expected) < SIGNIFICANCE:
            red = -change > th.sales_drop_red
            check.findings.append(_sales_finding(
                SALES_DROP, key, pid, c, RED if red else YELLOW, th.sales_drop_red if red else th.sales_drop))
        elif key in active and -change > th.sales_drop / 2:
            check.keep.add(key)  # ещё не вернулись в норму с запасом
    return check


def sales_growth(data: SalesData, th: Thresholds, active: set[str] = frozenset()) -> Check:
    """Рост продаж больше порога (200 %) — повод проверить цену: не слишком ли низкая."""
    check = Check(SALES_GROWTH)
    factors = weekday_factors(data)
    for key, pid, series in _subjects(data):
        c = compare(series, data, factors)
        if c is None:
            check.keep.add(key)
            continue
        change = c.change_pct
        if change is None:  # раньше не продавалось — роста в процентах нет
            continue
        if change > th.sales_growth and poisson_ge(c.actual, c.expected) < SIGNIFICANCE:
            check.findings.append(_sales_finding(SALES_GROWTH, key, pid, c, YELLOW, th.sales_growth))
        elif key in active and change > th.sales_growth / 2:
            check.keep.add(key)
    return check


# ── цена и комиссия ──
@dataclass(frozen=True)
class Point:
    at: datetime
    value: Decimal | None


def change_points(points: list[Point], now: datetime, window: timedelta = CHANGE_WINDOW) -> tuple[Point, Point] | None:
    """(с чем сравниваем, сейчас): значение на начало окна — последний снимок не позже now − window, а если снимков
    тогда ещё не было — первый; сейчас — последний снимок. Значения нет или 0 («не задана» у Ozon) — None."""
    if not points:
        return None
    points = sorted(points, key=lambda p: p.at)
    start = now - window
    ref = next((p for p in reversed(points) if p.at <= start), points[0])
    cur = points[-1]
    if not ref.value or not cur.value or ref.value <= 0 or cur.value <= 0:
        return None
    return ref, cur


def price_change(
    prices: dict[uuid.UUID, list[Point]], now: datetime, th: Thresholds,
    via_service: dict[uuid.UUID, list[datetime]] | None = None,
) -> Check:
    """Цена изменилась больше чем на порог (жёлтая 10 %, красная 30 %) за 7 дней. Изменения через сервис (шаги 6.2–6.4) —
    тоже аномалия (решение владельца 17.10.2026), с пометкой «через сервис»."""
    check = Check(PRICE_CHANGE)
    for pid, points in prices.items():
        pair = change_points(points, now)
        if pair is None:
            check.keep.add(product_key(pid))
            continue
        ref, cur = pair
        pct = (cur.value / ref.value - 1) * 100
        if abs(pct) <= th.price:
            continue
        red = abs(pct) > th.price_red
        service = any(now - CHANGE_WINDOW < t <= now for t in (via_service or {}).get(pid, []))
        check.findings.append(Finding(
            PRICE_CHANGE, product_key(pid), RED if red else YELLOW, pid, metric_before=ref.value, metric_after=cur.value,
            threshold=Decimal(th.price_red if red else th.price),
            details={"change_pct": float(pct.quantize(Decimal("0.1"), ROUND_HALF_UP)), "changed_at": cur.at.isoformat(),
                     "via_service": service},
        ))
    return check


def commission_change(commissions: dict[uuid.UUID, list[Point]], now: datetime, th: Thresholds) -> Check:
    """Комиссия Ozon изменилась больше чем на порог (2 п. п.) за 7 дней."""
    check = Check(COMMISSION_CHANGE)
    for pid, points in commissions.items():
        pair = change_points(points, now)
        if pair is None:
            check.keep.add(product_key(pid))
            continue
        ref, cur = pair
        pp = cur.value - ref.value
        if abs(pp) > th.commission_pp:
            check.findings.append(Finding(
                COMMISSION_CHANGE, product_key(pid), YELLOW, pid, metric_before=ref.value, metric_after=cur.value,
                threshold=th.commission_pp, details={"change_pp": float(pp), "changed_at": cur.at.isoformat()},
            ))
    return check


# ── маржа, остатки, акции ──
@dataclass(frozen=True)
class MarginItem:
    """Товар, который продаётся: не в архиве, есть остаток или были продажи за 30 дней (решение владельца 17.10.2026)."""

    product_id: uuid.UUID
    margin: Decimal | None  # калькулятор цены (шаг 4.2) при текущей цене; нечем посчитать — None
    price: Decimal | None


def low_margin(items: list[MarginItem], th: Thresholds) -> Check:
    """Маржа при текущей цене ниже порога пользователя (10 %) — красная."""
    check = Check(LOW_MARGIN)
    for it in items:
        key = product_key(it.product_id)
        if it.margin is None:
            check.keep.add(key)
        elif it.margin < th.low_margin:
            check.findings.append(Finding(
                LOW_MARGIN, key, RED, it.product_id, metric_after=it.margin, threshold=th.low_margin,
                details={"price": str(it.price) if it.price is not None else None},
            ))
    return check


@dataclass(frozen=True)
class StockItem:
    product_id: uuid.UUID
    status: str | None  # статус остатка шага 5.1: critical | low | normal | excess | None
    days_of_supply: Decimal | None
    available: int
    avg_daily: Decimal | None


def low_stock(items: list[StockItem], th: Thresholds, sales_known: bool) -> Check:
    """Дней запаса меньше «Мало» (14) — жёлтая, меньше «Критично» (7) — красная. Продажи неизвестны — не судим."""
    check = Check(LOW_STOCK, keep_all=not sales_known)
    if not sales_known:
        return check
    for it in items:
        if it.status not in ("critical", "low"):
            continue
        red = it.status == "critical"
        check.findings.append(Finding(
            LOW_STOCK, product_key(it.product_id), RED if red else YELLOW, it.product_id,
            metric_after=it.days_of_supply, threshold=Decimal(th.stock_critical if red else th.stock_low),
            details={"available": it.available, "avg_daily": str(it.avg_daily) if it.avg_daily is not None else None},
        ))
    return check


@dataclass(frozen=True)
class PromoItem:
    """Текущее участие товара в незакончившейся акции."""

    product_id: uuid.UUID
    promotion_id: uuid.UUID
    title: str
    action_price: Decimal | None
    added_automatically: bool
    margin: Decimal | None  # маржа в акции калькулятором 4.2; нечем посчитать — None
    date_to: datetime | None = None


def _promo_details(it: PromoItem) -> dict:
    return {"promotion_id": str(it.promotion_id), "promotion": it.title,
            "action_price": str(it.action_price) if it.action_price is not None else None,
            "date_to": it.date_to.isoformat() if it.date_to else None}


def promo_auto_added(items: list[PromoItem]) -> Check:
    """Ozon добавил товар в акцию сам (автодобавление) — сразу жёлтая; закрывается, когда товар вышел или акция кончилась."""
    check = Check(PROMO_AUTO_ADDED)
    for it in items:
        if it.added_automatically:
            check.findings.append(Finding(
                PROMO_AUTO_ADDED, promo_key(it.product_id, it.promotion_id), YELLOW, it.product_id,
                metric_after=it.margin, details=_promo_details(it),
            ))
    return check


def promo_loss(items: list[PromoItem]) -> Check:
    """Маржа в акции ниже нуля — каждая продажа по цене акции в минус — красная."""
    check = Check(PROMO_LOSS)
    for it in items:
        key = promo_key(it.product_id, it.promotion_id)
        if it.margin is None:
            check.keep.add(key)
        elif it.margin < 0:
            check.findings.append(Finding(
                PROMO_LOSS, key, RED, it.product_id, metric_after=it.margin, threshold=Decimal(0),
                details={**_promo_details(it), "auto": it.added_automatically},
            ))
    return check


# ── выплаты Ozon (шаг 3.6) — без банка ──
def payout_missed(
    overdue: list[date], payout_days: set[date], expected: dict[date, Decimal | None], active: set[str] = frozenset()
) -> Check:
    """По графику ожидалась выплата, а её нет (просрочка шага 3.6: дни графика за 2 недели, окно 4 дня, только по
    загруженным дням) — красная. Закрывается, когда после ожидаемого дня пришла любая выплата."""
    check = Check(PAYOUT_MISSED)
    for day in overdue:
        amount = expected.get(day)
        check.findings.append(Finding(PAYOUT_MISSED, day_key("payout", day), RED, metric_before=amount,
                                      details={"day": day.isoformat()}))
    found = {f.key for f in check.findings}
    for key in active - found:
        day = date.fromisoformat(key.split(":", 1)[1])
        if not any(p >= day for p in payout_days):
            check.keep.add(key)  # выплаты так и нет — просрочка не прошла, хоть и старше двух недель
    return check


@dataclass(frozen=True)
class PayoutFact:
    day: date
    amount: Decimal
    expected: Decimal | None  # по графику: начисления недели продаж минус комиссия графика
    week_from: date | None = None


def payout_diff(facts: list[PayoutFact], today: date, th: Thresholds, reliable: bool = True) -> Check:
    """Выплата за последние 14 дней разошлась с прогнозом по графику больше чем на порог (10 %) — жёлтая. График,
    определённый по выплатам с низкой уверенностью, не сверяем (reliable=False): расхождения были бы каждую неделю."""
    check = Check(PAYOUT_DIFF, keep_all=not reliable)
    if not reliable:
        return check
    for f in facts:
        if f.day < today - timedelta(days=PAYOUT_DIFF_DAYS) or f.expected is None or f.expected <= 0:
            continue
        pct = (f.amount / f.expected - 1) * 100
        if abs(pct) > th.payout_diff:
            check.findings.append(Finding(
                PAYOUT_DIFF, day_key("payout", f.day), YELLOW, metric_before=f.expected, metric_after=f.amount,
                threshold=Decimal(th.payout_diff),
                details={"day": f.day.isoformat(), "change_pct": float(pct.quantize(Decimal("0.1"), ROUND_HALF_UP)),
                         "week_from": f.week_from.isoformat() if f.week_from else None},
            ))
    return check


# ── выплаты Ozon ↔ банк (шаг 3.11) ──
@dataclass(frozen=True)
class BankRow:
    """Строка сверки `bank.ozon_check`: ok, diff, no_bank (выписка за эти дни есть, поступления нет), waiting."""

    payout_day: date
    status: str
    ozon_amount: Decimal | None
    bank_amount: Decimal | None = None
    bank_day: date | None = None
    diff: Decimal | None = None


def _bank_details(r: BankRow) -> dict:
    return {"day": r.payout_day.isoformat(), "bank_day": r.bank_day.isoformat() if r.bank_day else None}


def bank_missing(rows: list[BankRow], enabled: bool) -> Check:
    """Выплата Ozon не дошла до счёта: выписка за эти дни загружена, а поступления нет — красная."""
    check = Check(BANK_MISSING, off=not enabled)
    for r in rows if enabled else []:
        key = day_key("payout", r.payout_day)
        if r.status == "no_bank":
            check.findings.append(Finding(BANK_MISSING, key, RED, metric_before=r.ozon_amount, details=_bank_details(r)))
        elif r.status == "waiting":
            check.keep.add(key)  # выписки за эти дни ещё нет
    return check


def bank_diff(rows: list[BankRow], enabled: bool) -> Check:
    """На счёт пришло не столько, сколько выплатил Ozon (от 1 ₽) — жёлтая."""
    check = Check(BANK_DIFF, off=not enabled)
    for r in rows if enabled else []:
        key = day_key("payout", r.payout_day)
        if r.status == "diff" and r.diff is not None and abs(r.diff) >= BANK_DIFF_MIN:
            check.findings.append(Finding(BANK_DIFF, key, YELLOW, metric_before=r.ozon_amount, metric_after=r.bank_amount,
                                          threshold=BANK_DIFF_MIN, details=_bank_details(r)))
        elif r.status == "waiting":
            check.keep.add(key)
    return check


# ── планирование (шаги 3.8–3.9) — только если оно включено ──
@dataclass(frozen=True)
class CashDay:
    day: date
    balance: Decimal | None  # остаток на конец дня по прогнозу


def cash_gap(
    days: list[CashDay], today: date, th: Thresholds, threshold: Decimal | None, enabled: bool, start_known: bool
) -> Check:
    """Прогноз кассового разрыва в ближайшие N дней (14): остаток уйдёт ниже нуля — красная, ниже порога остатка
    пользователя — жёлтая. Остаток на счетах не указан — не судим."""
    check = Check(CASH_GAP, off=not enabled, keep_all=enabled and not start_known)
    if not enabled or not start_known:
        return check
    window = [d for d in days if today <= d.day < today + timedelta(days=th.cash_gap_days) and d.balance is not None]
    limit = threshold if threshold is not None and threshold > 0 else None
    below_zero = next((d for d in window if d.balance < 0), None)
    below_limit = next((d for d in window if limit is not None and d.balance < limit), None)
    first = below_zero or below_limit
    if first is None:
        return check
    low = min(window, key=lambda d: (d.balance, d.day))
    check.findings.append(Finding(
        CASH_GAP, "cash", RED if below_zero else YELLOW, metric_after=low.balance,
        threshold=Decimal(0) if below_zero else limit,
        details={"first_day": first.day.isoformat(), "min_day": low.day.isoformat(), "days": th.cash_gap_days},
    ))
    return check


@dataclass(frozen=True)
class OpItem:
    """Действующая операция без статьи (не перевод). first_seen — когда она появилась в сервисе (первая версия)."""

    op_id: uuid.UUID
    first_seen: datetime
    day: date
    amount: Decimal


def uncategorized(ops: list[OpItem], now: datetime, th: Thresholds, enabled: bool) -> Check:
    """Операции без статьи дольше N дней (7) — жёлтая: без статьи ДДС и прогноз по статьям неточны."""
    check = Check(UNCATEGORIZED, off=not enabled)
    if not enabled:
        return check
    old = [o for o in ops if o.first_seen <= now - timedelta(days=th.uncategorized_days)]
    if old:
        check.findings.append(Finding(
            UNCATEGORIZED, "operations", YELLOW, metric_after=Decimal(len(old)), threshold=Decimal(th.uncategorized_days),
            details={"count": len(old), "income": str(sum((o.amount for o in old if o.amount > 0), Decimal(0))),
                     "expense": str(sum((o.amount for o in old if o.amount < 0), Decimal(0))),
                     "oldest_day": min(o.day for o in old).isoformat()},
        ))
    return check


# ── сверка с активными ──
@dataclass(frozen=True)
class Active:
    id: uuid.UUID
    type: str
    key: str
    severity: str
    product_id: uuid.UUID | None = None


@dataclass
class Plan:
    create: list[Finding] = field(default_factory=list)
    resolve: dict[uuid.UUID, str] = field(default_factory=dict)  # id → normal | escalated | gone
    seen: list[uuid.UUID] = field(default_factory=list)  # активные, которые всё ещё есть

    @property
    def escalated(self) -> int:
        return sum(1 for r in self.resolve.values() if r == ESCALATED)


def reconcile(active: list[Active], checks: list[Check], gone_products: set[uuid.UUID] = frozenset()) -> Plan:
    """Что сделать с аномалиями. Типы, которых нет среди проверок (детектор не отработал), не трогаем."""
    plan = Plan()
    for check in checks:
        current = {a.key: a for a in active if a.type == check.type}
        if check.off:
            plan.resolve.update({a.id: GONE for a in current.values()})
            continue
        findings: dict[str, Finding] = {}
        for f in check.findings:  # один предмет — одна находка, самая серьёзная
            if f.key not in findings or _RANK[f.severity] > _RANK[findings[f.key].severity]:
                findings[f.key] = f
        for key, f in findings.items():
            a = current.get(key)
            if a is None:
                plan.create.append(f)
            elif _RANK[f.severity] > _RANK[a.severity]:
                plan.resolve[a.id] = ESCALATED  # жёлтая стала красной — новая запись, жёлтая остаётся в истории
                plan.create.append(f)
            else:
                plan.seen.append(a.id)
        for key, a in current.items():
            if key in findings or check.keep_all or key in check.keep:
                continue
            plan.resolve[a.id] = GONE if a.product_id is not None and a.product_id in gone_products else NORMAL
    return plan
