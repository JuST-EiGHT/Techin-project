"""Планирование поставок (шаг 5.2, BUSINESS_LOGIC §5). Чистые функции — без БД и HTTP.

Считаем по паре «товар × куда везти»: для складов Ozon (FBO) — кластер покупателя (Ozon принимает поставку в кластер),
для своих складов (FBS / rFBS) — склад продавца, и там это закупка, а не поставка в Ozon.

    avg_daily    = продажи за 30 дней / 30 (дней с данными, если загружено меньше)
    trend        = (продажи за 7 дней / 7) / avg_daily
    days_left    = остаток / (avg_daily × trend)          — остаток на складе сейчас, без «в пути»
    target_stock = avg_daily × target_days                — запас после поставки (по умолчанию 45 дней)
    to_order     = max(0, avg_daily × (target_days + lead_time_days) − (остаток + в пути))
                   пока поставка едет (lead_time_days, по умолчанию 7), товар продаётся — довозим и это
    срочность:   days_left < порог «Критично» (7) — critical, < порог «Мало» (14) — urgent, остальные — planned

Только пары с продажами за 30 дней. Нечего везти (to_order = 0) — рекомендации нет. Продаж за 7 дней нет — тренд 0,
остаток не убывает: срочности нет (planned), но запас до цели всё равно довозим.
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
MIN_DAYS = 7  # меньше дней с данными о заказах — не считаем: среднее по паре дней обманчиво

_TENTH = Decimal("0.1")
_RATE = Decimal("0.001")


@dataclass(frozen=True)
class Params:
    target_days: int = DEFAULT_TARGET_DAYS  # на сколько дней продаж запас после поставки
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS  # сколько дней поставка в пути


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
    need = math.ceil(avg * (params.target_days + params.lead_time_days))
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
        target_stock=math.ceil(avg * params.target_days), need=need, to_order=to_order, urgency=urgency,
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
