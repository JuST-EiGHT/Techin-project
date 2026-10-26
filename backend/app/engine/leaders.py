"""Лучшие и худшие товары периода (шаг 10.7, «Обзор бизнеса»). Чистый модуль — без БД и HTTP.

- **По прибыли** — прибыль товара за период из «Прибыли по товарам» (3.4): лучшие — с наибольшей прибылью (больше нуля),
  худшие — с наименьшей из остальных (в минусе — первыми). Без себестоимости прибыль не посчитать — такие товары не
  участвуют, их число — отдельно (правило 5).
- **По изменению продаж** — проданные штуки за период против прошлого сопоставимого периода (`sales_daily`, только дни с
  данными): ожидали = продано в прошлом периоде / его дни с данными × дни с данными сейчас. Рост или падение — только если
  это не случайность, как в аномалиях 7.1: у малых продаж (Пуассон) вероятность такого числа меньше 1 %. Не продавалось
  раньше, а теперь продаётся — рост «с нуля» (процента нет); ожидание не меньше 0,5 шт., иначе ростом считалась бы любая
  единственная продажа (так рост «с нуля» — от 4 шт.). Порядок — по изменению в штуках.
"""
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.engine.anomaly_detector import SIGNIFICANCE, poisson_ge, poisson_le

LIMIT = 5
MIN_DAYS = 7  # дней с данными в каждом периоде — меньше не сравниваем продажи
MIN_EXPECTED = 0.5  # ожидание «с нуля» — не меньше полштуки за период


@dataclass(frozen=True)
class ProfitRow:
    offer_id: str
    product_id: uuid.UUID | None
    name: str | None
    revenue: Decimal
    profit: Decimal | None  # нет себестоимости — None
    margin: Decimal | None
    qty: int


@dataclass(frozen=True)
class SalesChange:
    offer_id: str
    product_id: uuid.UUID | None
    name: str | None
    qty: int  # продано сейчас
    prev_qty: int  # продано в прошлом периоде
    expected: Decimal  # ожидали за дни с данными сейчас
    change_pct: Decimal | None  # к ожидаемому; раньше не продавалось — None

    @property
    def delta(self) -> Decimal:
        return Decimal(self.qty) - self.expected


def by_profit(rows: list[ProfitRow], limit: int = LIMIT) -> tuple[list[ProfitRow], list[ProfitRow]]:
    """(лучшие, худшие). Лучшие — прибыль больше нуля, по убыванию; худшие — из остальных, по возрастанию."""
    known = [r for r in rows if r.profit is not None]
    best = sorted((r for r in known if r.profit > 0), key=lambda r: (-r.profit, r.offer_id))[:limit]
    taken = {r.offer_id for r in best}
    worst = sorted((r for r in known if r.offer_id not in taken), key=lambda r: (r.profit, r.offer_id))[:limit]
    return best, worst


UP, DOWN = "up", "down"


@dataclass(frozen=True)
class QtyChange:
    expected: Decimal  # ожидали за дни с данными сейчас
    change_pct: Decimal | None  # к ожидаемому; раньше не продавалось — None
    direction: str | None  # up | down — значимое изменение; None — в пределах случайных колебаний


def compare_qty(qty: int, prev: int, days: int, prev_days: int) -> QtyChange | None:
    """Продано сейчас против прошлого периода с поправкой на дни с данными; значимость — как в аномалиях 7.1.
    Меньше MIN_DAYS дней с данными в одном из периодов — None (не сравниваем). «Аналитика продаж» (10.8) — для таблицы."""
    if days < MIN_DAYS or prev_days < MIN_DAYS:
        return None
    expected = Decimal(prev) / prev_days * days
    lam = float(expected)
    pct = None if prev == 0 else ((qty / expected - 1) * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    direction = None
    if qty > expected and poisson_ge(qty, max(lam, MIN_EXPECTED)) < SIGNIFICANCE:
        direction = UP
    elif qty < expected and poisson_le(qty, lam) < SIGNIFICANCE:
        direction = DOWN
    return QtyChange(expected.quantize(Decimal("0.1"), ROUND_HALF_UP), pct, direction)


def sales_changes(
    current: dict[str, int], previous: dict[str, int], days: int, prev_days: int,
    info: dict[str, tuple[uuid.UUID | None, str | None]] | None = None, limit: int = LIMIT,
) -> tuple[list[SalesChange], list[SalesChange]] | None:
    """(растут, падают) — только значимые изменения. Дней с данными мало в одном из периодов — None (не сравниваем)."""
    if days < MIN_DAYS or prev_days < MIN_DAYS:
        return None
    info = info or {}
    growth: list[SalesChange] = []
    decline: list[SalesChange] = []
    for offer in sorted(set(current) | set(previous)):
        qty, prev = current.get(offer, 0), previous.get(offer, 0)
        c = compare_qty(qty, prev, days, prev_days)
        pid, name = info.get(offer, (None, None))
        item = SalesChange(offer, pid, name, qty, prev, c.expected, c.change_pct)
        if c.direction == UP:
            growth.append(item)
        elif c.direction == DOWN:
            decline.append(item)
    growth.sort(key=lambda x: (-x.delta, x.offer_id))
    decline.sort(key=lambda x: (x.delta, x.offer_id))
    return growth[:limit], decline[:limit]
