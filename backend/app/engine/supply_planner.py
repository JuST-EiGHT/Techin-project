"""Планирование поставок (шаг 5.2, BUSINESS_LOGIC §5). Чистые функции — без БД и HTTP.

Считаем по паре «товар × куда везти»: для складов Ozon (FBO) — кластер покупателя (Ozon принимает поставку в кластер),
для своих складов (FBS / rFBS) — склад продавца, и там это закупка, а не поставка в Ozon.

    avg_daily    = продажи за 30 дней / 30 (дней с данными, если загружено меньше)
    trend        = (продажи за 7 дней / 7) / avg_daily
    days_left    = остаток / (avg_daily × trend)          — остаток на складе сейчас, без «в пути»
    target_stock = avg_daily × (target_days + safety_days) — запас после поставки (по умолчанию 45 дней + страховой 0)
    to_order     = max(0, avg_daily × (target_days + safety_days + lead_time_days) − (остаток + в пути))
                   пока поставка едет (lead_time_days, по умолчанию 7), товар продаётся — довозим и это
    срочность:   days_left < порог «Критично» (7) — critical, < порог «Мало» (14) — urgent, остальные — planned

Только пары с продажами за 30 дней. Нечего везти (to_order = 0) — рекомендации нет. Продаж за 7 дней нет — тренд 0,
остаток не убывает: срочности нет (planned), но запас до цели всё равно довозим.

Шаг 10.18a — склады Ozon (FBO) считаются по данным Ozon о кластере (`cluster_recommend`, решения владельца 10.11.2026):
    продажи в день  = ads_cluster Ozon (28 дней, как в «Управлении остатками»); остаток и в пути — склады этого кластера
    по продажам     = max(0, ⌈продажи в день × (target + safety + lead)⌉ − остаток − в пути) — не больше запаса на срок
    везти           = max(по продажам, рекомендованная поставка Ozon) — рекомендация Ozon — нижняя планка, «прямо
                      необходимое»; кластер уже затоварен и Ozon не советует — ничего не везём
    минимум         = правка 20.11 (решение владельца): `min_qty` шт. на всю поставку в кластер (коробка, по умолчанию 6),
                      а не на каждый товар. Кластеру нужно меньше — довозим до минимума по штуке тому товару кластера,
                      у кого после поставки запас самый короткий (продаётся лучше — получает больше), пока у него не
                      больше `topup_days` дней (по умолчанию 60); не выходит — в кластер не везём (`plan_clusters`)
    хватит на       = остаток / продажи в день (как idc_cluster); срочность — по порогам остатков, как выше
    продажи для «хватит после» — ads; продаж Ozon в кластере не видно, а рекомендация есть — спрос из неё:
                      (рекомендация + остаток + в пути) / срок рекомендации (`ozon_days`)
Закупка на свой склад (FBS / rFBS) — прежним расчётом `recommend` по заказам.

Пары, которые везти не нужно, тоже показываем (как Ozon, `cluster_skip`): «Пока хватает» — продаётся, запаса хватит на
срок; «Не поставлять» — продаж в кластере нет или за срок продастся меньше минимума.
"""
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.engine.stocks import DEFAULT_THRESHOLDS, Thresholds

SUPPLY, PURCHASE = "supply", "purchase"  # поставка в кластер Ozon / закупка на свой склад
CRITICAL, URGENT, PLANNED = "critical", "urgent", "planned"
URGENCIES = (CRITICAL, URGENT, PLANNED)
DEFAULT_TARGET_DAYS = 45
DEFAULT_LEAD_TIME_DAYS = 7
DEFAULT_SAFETY_DAYS = 0  # страховой запас (шаг 10.18) — сверх цели, на случай задержки поставки или всплеска спроса
DEFAULT_MIN_QTY = 6  # минимум в поставке в кластер, шт. (коробка; с 20.11 — на всю поставку, не на товар) — настройка
DEFAULT_TOPUP_DAYS = 60  # 20.11: довозим до минимума, если запаса хватит не больше чем на столько дней — настройка
DEFAULT_OZON_DAYS = 28  # на сколько дней считает рекомендацию Ozon (supply_period) — настройка
OZON, SALES = "ozon", "sales"  # 10.18a: на чём основано «везти» — рекомендация Ozon или продажи в кластере
ENOUGH, IDLE = "enough", "idle"  # 10.18a: «Пока хватает» / «Не поставлять» — у пар, которые везти не нужно
COVERED, NO_SALES, BELOW_MIN = "covered", "no_sales", "below_min"  # почему не везём
MIN_DAYS = 7  # меньше дней с данными о заказах — не считаем: среднее по паре дней обманчиво

_TENTH = Decimal("0.1")
_RATE = Decimal("0.001")


@dataclass(frozen=True)
class Params:
    target_days: int = DEFAULT_TARGET_DAYS  # на сколько дней продаж запас после поставки
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS  # сколько дней поставка в пути
    safety_days: int = DEFAULT_SAFETY_DAYS  # страховой запас, дней продаж


@dataclass(frozen=True)
class Demand:
    sold_30: int  # продано (заказано, без отмен) за 30 дней
    sold_7: int  # за последние 7 из них
    days_30: int = 30  # дней с данными в окне 30 дней
    days_7: int = 7


@dataclass
class Point:
    """Товар и куда его везти: кластер Ozon (supply) или свой склад (purchase)."""

    product: Any
    offer_id: str
    kind: str  # supply | purchase
    destination: str  # ключ кластера или склада
    stock: int  # на складах этого кластера (свободный остаток своего склада)
    in_transit: int  # уже едет туда
    demand: Demand


@dataclass
class SupplyRecommendation:
    point: Point
    avg_daily: Decimal  # продажи в день за 30 дней
    rate: Decimal  # avg_daily × trend — продажи в день за 7 дней
    trend: Decimal | None  # None — продаж за 30 дней нет
    days_left: Decimal | None  # None — за 7 дней не продавалось, остаток не убывает
    target_stock: int
    need: int  # нужно иметь к приходу поставки: продажи за target_days + lead_time_days
    to_order: int
    urgency: str
    arrives_late: bool  # кончится раньше, чем доедет поставка
    # 10.18a — только у кластеров Ozon: что советует Ozon, сколько выходит по продажам, на чём основано, довезли ли до
    # минимума и на сколько дней хватит после поставки
    ozon_recommended: int | None = None
    own_need: int | None = None
    basis: str | None = None
    min_applied: bool = False
    days_after: Decimal | None = None
    topup: int = 0  # 20.11: сколько штук добавили, чтобы поставка в кластер дотянула до минимума


def recommend(
    point: Point, params: Params = Params(), thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> SupplyRecommendation | None:
    d = point.demand
    if d.sold_30 <= 0 or d.days_30 <= 0:
        return None
    avg = Decimal(d.sold_30) / d.days_30
    rate = Decimal(d.sold_7) / d.days_7 if d.days_7 > 0 else avg
    trend = rate / avg
    stock = max(point.stock, 0)
    days_left = (Decimal(stock) / rate).quantize(_TENTH, ROUND_HALF_UP) if rate > 0 else None
    need = math.ceil(avg * (params.target_days + params.safety_days + params.lead_time_days))
    to_order = max(0, need - stock - max(point.in_transit, 0))
    if to_order == 0:
        return None
    if days_left is not None and days_left < thresholds.critical:
        urgency = CRITICAL
    elif days_left is not None and days_left < thresholds.low:
        urgency = URGENT
    else:
        urgency = PLANNED
    return SupplyRecommendation(
        point=point, avg_daily=avg.quantize(_RATE, ROUND_HALF_UP), rate=rate.quantize(_RATE, ROUND_HALF_UP),
        trend=trend.quantize(Decimal("0.01"), ROUND_HALF_UP), days_left=days_left,
        target_stock=math.ceil(avg * (params.target_days + params.safety_days)), need=need, to_order=to_order, urgency=urgency,
        arrives_late=days_left is not None and days_left < params.lead_time_days,
    )


def plan(
    points: list[Point], params: Params = Params(), thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> list[SupplyRecommendation]:
    """Рекомендации: сначала срочные, внутри — у кого запас кончится раньше, потом больше везти."""
    out = [r for r in (recommend(p, params, thresholds) for p in points) if r is not None]
    never = Decimal(10 ** 9)
    return sorted(out, key=lambda r: (
        URGENCIES.index(r.urgency), never if r.days_left is None else r.days_left, -r.to_order, r.point.offer_id,
        r.point.destination,
    ))


# ── 10.18a: склады Ozon — по данным Ozon о кластере ──
@dataclass
class ClusterPoint:
    """Товар и кластер Ozon: остаток и в пути на складах кластера, продажи в день по Ozon, рекомендация Ozon."""

    product: Any
    offer_id: str
    destination: str  # ключ кластера
    stock: int
    in_transit: int
    ads: Decimal | None  # продажи в день в кластере по Ozon (ads_cluster); None — Ozon по кластеру не считал
    ozon_rec: int | None  # рекомендованная поставка Ozon, шт.; None — рекомендации не загружены
    attention: str | None = None  # attention_level Ozon
    kind: str = SUPPLY
    demand: Demand | None = None  # заказов по кластеру больше не считаем — продажи от Ozon


def _urgency(days_left: Decimal | None, thresholds: Thresholds, attention: str | None = None) -> str:
    if days_left is not None and days_left < thresholds.critical:
        return CRITICAL
    if days_left is not None and days_left < thresholds.low:
        return URGENT
    if days_left is None and (attention or "").upper() in ("HIGH", "CRITICAL"):
        return URGENT  # продаж Ozon по кластеру нет, но Ozon считает поставку важной
    return PLANNED


def _demand(point: ClusterPoint, ozon_days: int) -> Decimal | None:
    """Продажи в день для «хватит после поставки»: Ozon по кластеру (ads); продаж в кластере не видно, но Ozon советует
    поставку — спрос из рекомендации: (рекомендация + остаток + в пути) / срок, на который она посчитана."""
    if point.ads is not None and point.ads > 0:
        return point.ads
    rec = max(point.ozon_rec or 0, 0)
    if rec > 0 and ozon_days > 0:
        return Decimal(rec + max(point.stock, 0) + max(point.in_transit, 0)) / ozon_days
    return None


def _days(units: int, rate: Decimal | None) -> Decimal | None:
    return (Decimal(units) / rate).quantize(_TENTH, ROUND_HALF_UP) if rate else None


def cluster_recommend(
    point: ClusterPoint, params: Params = Params(), thresholds: Thresholds = DEFAULT_THRESHOLDS,
    ozon_days: int = DEFAULT_OZON_DAYS, *, extra: int = 0,
) -> SupplyRecommendation | None:
    """Сколько везти товара в кластер без учёта минимума: max(по продажам, рекомендация Ozon) + `extra` (довозка до
    минимума кластера, считает `plan_clusters`); нечего везти — None."""
    horizon = params.target_days + params.safety_days + params.lead_time_days
    ads = point.ads if point.ads is not None and point.ads > 0 else None
    stock, transit = max(point.stock, 0), max(point.in_transit, 0)
    need = math.ceil(ads * horizon) if ads else 0
    own = max(0, need - stock - transit)
    rec = max(point.ozon_rec or 0, 0)
    base = max(own, rec)
    qty = base + extra
    if qty <= 0:
        return None
    days_left = (Decimal(stock) / ads).quantize(_TENTH, ROUND_HALF_UP) if ads else None
    return SupplyRecommendation(
        point=point,  # type: ignore[arg-type]
        avg_daily=(ads or Decimal(0)).quantize(_RATE, ROUND_HALF_UP),
        rate=(ads or Decimal(0)).quantize(_RATE, ROUND_HALF_UP), trend=None, days_left=days_left,
        target_stock=math.ceil(ads * (params.target_days + params.safety_days)) if ads else 0,
        need=need, to_order=qty, urgency=_urgency(days_left, thresholds, point.attention),
        arrives_late=days_left is not None and days_left < params.lead_time_days,
        ozon_recommended=point.ozon_rec, own_need=own if ads else None,
        basis=(OZON if rec > 0 and rec >= own else SALES) if base > 0 else None, min_applied=extra > 0,
        days_after=_days(stock + transit + qty, _demand(point, ozon_days)), topup=extra,
    )


@dataclass
class ClusterSkip:
    """Пара товар × кластер, которую везти не нужно, — для вида «все товары» (как в Ozon)."""

    point: ClusterPoint
    status: str  # ENOUGH | IDLE
    reason: str  # COVERED | NO_SALES | BELOW_MIN
    avg_daily: Decimal
    days_left: Decimal | None
    own_need: int | None
    planned: int = 0  # BELOW_MIN: сколько было нужно (по продажам или Ozon), но кластер не дотянул до минимума


def cluster_skip(point: ClusterPoint, params: Params = Params(), *, planned: int = 0) -> ClusterSkip:
    """Почему не везём: `planned` > 0 — товар был нужен, но поставка в кластер не дотягивает до минимума и до него
    запас вышел бы дольше срока — «Не поставлять»; продаж нет — тоже; иначе запаса хватает — «Пока хватает»."""
    horizon = params.target_days + params.safety_days + params.lead_time_days
    ads = point.ads if point.ads is not None and point.ads > 0 else None
    stock, transit = max(point.stock, 0), max(point.in_transit, 0)
    own = max(0, math.ceil(ads * horizon) - stock - transit) if ads else None
    if planned > 0:
        status, reason = IDLE, BELOW_MIN
    elif ads is None:
        status, reason = IDLE, NO_SALES
    else:
        status, reason = ENOUGH, COVERED
    return ClusterSkip(
        point=point, status=status, reason=reason, avg_daily=(ads or Decimal(0)).quantize(_RATE, ROUND_HALF_UP),
        days_left=(Decimal(stock) / ads).quantize(_TENTH, ROUND_HALF_UP) if ads else None, own_need=own,
        planned=planned,
    )


def _topup(points: list[ClusterPoint], base: dict[int, int], short: int, max_days: int,
           ozon_days: int) -> dict[int, int] | None:
    """Довезти `short` штук до минимума кластера: по штуке тому товару, у кого после поставки запас самый короткий (при
    равных — кто продаётся лучше), пока у него не больше `max_days` дней. Не выходит — None."""
    extra = {i: 0 for i in range(len(points))}
    for _ in range(short):
        best: tuple | None = None
        for i, p in enumerate(points):
            rate = _demand(p, ozon_days)
            if not rate:
                continue
            units = max(p.stock, 0) + max(p.in_transit, 0) + base.get(i, 0) + extra[i] + 1
            days = Decimal(units) / rate
            if days > max_days:
                continue
            key = (days, -rate, p.offer_id)
            if best is None or key < best[0]:
                best = (key, i)
        if best is None:
            return None
        extra[best[1]] += 1
    return extra


def plan_clusters(
    points: list[ClusterPoint], params: Params = Params(), thresholds: Thresholds = DEFAULT_THRESHOLDS,
    min_qty: int = DEFAULT_MIN_QTY, topup_days: int = DEFAULT_TOPUP_DAYS, ozon_days: int = DEFAULT_OZON_DAYS,
) -> tuple[list[SupplyRecommendation], list[ClusterSkip]]:
    """Поставки по кластерам (правка 20.11): у каждого товара — max(по продажам, Ozon); поставка в кластер — не меньше
    `min_qty` шт. всего; меньше — довозим (`_topup`), не выходит за `topup_days` дней — в кластер не везём, товары — «Не
    поставлять» с причиной. Возвращает (что везти, что не везти)."""
    by_dest: dict[str, list[ClusterPoint]] = {}
    for p in points:
        by_dest.setdefault(p.destination, []).append(p)
    recs: list[SupplyRecommendation] = []
    skips: list[ClusterSkip] = []
    for group in by_dest.values():
        base = {i: r.to_order for i, p in enumerate(group) if (r := cluster_recommend(p, params, thresholds, ozon_days))}
        total = sum(base.values())
        extra: dict[int, int] | None = {}
        if 0 < total < min_qty:
            extra = _topup(group, base, min_qty - total, topup_days, ozon_days)
        for i, p in enumerate(group):
            if extra is None:  # до минимума не дотянуть за срок — кластер не везём
                skips.append(cluster_skip(p, params, planned=base.get(i, 0)))
            elif (r := cluster_recommend(p, params, thresholds, ozon_days, extra=extra.get(i, 0))) is not None:
                recs.append(r)
            else:
                skips.append(cluster_skip(p, params))
    return sort_recs(recs), skips


def sort_recs(recs: list[SupplyRecommendation]) -> list[SupplyRecommendation]:
    """Сначала срочные; внутри — у кого запас кончится раньше; при равных — кластер с лучшими продажами (туда — больше),
    потом больше везти."""
    never = Decimal(10 ** 9)
    return sorted(recs, key=lambda r: (
        URGENCIES.index(r.urgency), never if r.days_left is None else r.days_left, -r.avg_daily, -r.to_order,
        r.point.offer_id, r.point.destination,
    ))
