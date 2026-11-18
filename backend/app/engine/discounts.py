"""Заявки покупателей на скидку (шаг 4.3a): состояние заявки, пометки и подсказка «по правилу».
Чистый модуль — без БД и HTTP.

Разбор ответа Ozon — `services/sync/discount_tasks.py` (OZON_API.md «Заявки на скидку»). Статусов у Ozon три: NEW, APPROVED,
DECLINED. «Истекла» — наше: новая заявка, у которой прошло время действия (`end_at`) — решать её уже поздно.

Пометки строки (решение — пользователя, сервис только подсказывает):
- «в минус» — запрошенная цена ниже минимальной цены для участия (калькулятор 4.2: при ней прибыль с продажи 0):
  каждая такая продажа убыточна; одобрить можно только с явным «понимаю» (решение владельца 17.11.2026);
- «ниже минимальной цены в Ozon» — ниже `min_auto_price` заявки (минимальная цена после автоприменения скидок и акций);
- «маржа ниже порога» — маржа при запрошенной цене от 0 до порога «низкой маржи» пользователя (шаг 7.1).
Подсказка «одобрили бы по правилу» — маржа при запрошенной цене не ниже порога из настроек (задел на автоматизацию
9.x: сервис сам ничего не одобряет).
"""
from datetime import datetime
from decimal import Decimal

NEW, APPROVED, DECLINED = "NEW", "APPROVED", "DECLINED"
EXPIRED = "EXPIRED"  # не статус Ozon: новая, у которой прошёл end_at
STATES = (NEW, APPROVED, DECLINED, EXPIRED)
STATE_TITLES = {NEW: "Новая", APPROVED: "Одобрена", DECLINED: "Отклонена", EXPIRED: "Истекла"}

# метки строки
LOSS, BELOW_OZON_MIN, LOW_MARGIN = "loss", "below_ozon_min", "low_margin"
MARK_TITLES = {
    LOSS: "В минус",
    BELOW_OZON_MIN: "Ниже мин. цены в Ozon",
    LOW_MARGIN: "Маржа ниже порога",
}


def state(status: str, end_at: datetime | None, now: datetime) -> str:
    """Состояние для вкладок: новая, у которой прошёл срок, — «истекла»; остальное — статус Ozon."""
    if status == NEW and end_at is not None and end_at <= now:
        return EXPIRED
    return status


def can_decide(status: str, end_at: datetime | None, now: datetime) -> bool:
    """Решать можно только новую заявку, пока она действует."""
    return state(status, end_at, now) == NEW


def hours_left(end_at: datetime | None, now: datetime) -> Decimal | None:
    if end_at is None:
        return None
    return Decimal(max((end_at - now).total_seconds(), 0) / 3600).quantize(Decimal("0.1"))


def discount_pct(price: Decimal | None, request: Decimal | None) -> Decimal | None:
    """Скидка запрошенной цены от вашей, %."""
    if not price or request is None or price <= 0:
        return None
    return ((price - request) / price * 100).quantize(Decimal("0.1"))


def marks(price: Decimal | None, breakeven: Decimal | None, ozon_min: Decimal | None, margin: Decimal | None,
          low_margin: Decimal) -> list[str]:
    """Пометки цены `price` (запрошенной или своей): в минус, ниже минимальной цены Ozon, маржа ниже порога."""
    out = []
    if price is None:
        return out
    loss = (breakeven is not None and price < breakeven) or (margin is not None and margin < 0)
    if loss:
        out.append(LOSS)
    if ozon_min is not None and price < ozon_min:
        out.append(BELOW_OZON_MIN)
    if not loss and margin is not None and margin < low_margin:
        out.append(LOW_MARGIN)
    return out


def rule_ok(margin: Decimal | None, rule_margin: Decimal | None) -> bool | None:
    """«Одобрили бы по правилу „маржа ≥ X %“»: правило не задано или маржу не посчитать — None (подсказки нет)."""
    if rule_margin is None or margin is None:
        return None
    return margin >= rule_margin
