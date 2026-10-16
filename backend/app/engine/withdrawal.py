"""Кандидаты на вывоз со складов Ozon (шаг 5.1, BUSINESS_LOGIC §6). Чистые функции — без БД и HTTP.

Логика владельца для выгрузки «Управление остатками»: товары по «дней без продаж» — хуже первыми; вывозим почти весь
остаток, оставляя 2 шт. в каждом кластере; у нормально продающихся — оставляем 10+. Числа и исключённые кластеры —
параметры пользователя (по умолчанию 2 и 10, без ограничения по штукам, ни один кластер не исключён).

Кто кандидат — товар со статусом «Избыток» (engine/stocks: запаса больше порога «Избыток» или не продаётся при
остатке) и остатком на складах Ozon (FBO; со своих складов FBS / rFBS вывозить нечего). Продажи неизвестны — не
кандидат: без них не отличить залежавшийся товар от хорошо продающегося.
- не продаётся (продаж за 30 дней нет) — в каждом кластере оставляем `keep_idle` шт., остальное — на вывоз;
- продаётся — в кластере оставляем `keep_selling` шт., и всего вывозим не больше излишка: остатка сверх запаса на
  `excess` дней (после вывоза запаса хватает ровно до порога «Избыток» — товар не кончится).
Порядок: дольше не продававшиеся первыми (продаж в данных нет — самые первые), при равных — больше дней запаса,
больше остаток. Внутри товара — сначала кластеры с большим остатком, в кластере — склады с большим остатком.
Лимит «вывезти всего» — по этому порядку: последняя строка может войти частично.
"""
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.engine.stocks import DEFAULT_THRESHOLDS, EXCESS, Thresholds, days_of_supply, stock_status

IDLE, SELLING = "idle", "selling"  # не продаётся / продаётся, но запаса слишком много
DEFAULT_KEEP_IDLE = 2
DEFAULT_KEEP_SELLING = 10
MAX_KEEP = 10_000

_NEVER = 10 ** 9  # «продаж в данных нет» — хуже любого числа дней без продаж


@dataclass(frozen=True)
class Params:
    total: int | None = None  # сколько штук вывезти всего; None — сколько получится
    keep_idle: int = DEFAULT_KEEP_IDLE  # оставлять в кластере у товара без продаж
    keep_selling: int = DEFAULT_KEEP_SELLING  # оставлять в кластере у продающегося
    excluded: frozenset[str] = frozenset()  # кластеры, которые не трогаем (id кластера Ozon строкой)


@dataclass(frozen=True)
class Place:
    """Остаток товара на одном складе Ozon (FBO)."""

    warehouse_id: Any
    warehouse: str
    cluster_key: str | None  # id кластера Ozon строкой; нет — склад сам по себе
    cluster: str | None
    present: int


@dataclass
class Item:
    key: Any  # id товара
    offer_id: str
    available: int  # доступно к продаже на всех складах, включая свои (FBS / rFBS)
    avg_daily: Decimal | None  # продажи в день; None — мало данных
    days_without_sales: int | None  # None — продаж в данных нет
    places: list[Place] = field(default_factory=list)  # склады Ozon (FBO) с остатком


@dataclass
class Line:
    place: Place
    qty: int  # вывезти, шт.
    cluster_total: int  # остаток товара в кластере до вывоза
    cluster_left: int = 0  # останется в кластере после вывоза всех строк товара в этом кластере


def _cluster_of(p: Place) -> str:
    return p.cluster_key if p.cluster_key is not None else f"warehouse:{p.warehouse_id}"


def settle(lines: list[Line]) -> list[Line]:
    """Сколько останется в кластере — по всем строкам товара в этом кластере."""
    out: dict[str, int] = {}
    for line in lines:
        out[_cluster_of(line.place)] = out.get(_cluster_of(line.place), 0) + line.qty
    for line in lines:
        line.cluster_left = line.cluster_total - out[_cluster_of(line.place)]
    return lines


@dataclass
class Candidate:
    item: Item
    kind: str  # idle | selling
    keep: int  # оставляем в каждом кластере, шт.
    surplus: int | None  # продающийся: остаток сверх запаса на `excess` дней; у непродающегося — None
    days_of_supply: Decimal | None
    possible: int  # можно вывезти без лимита, шт.
    lines: list[Line]  # с учётом лимита
    excluded_units: int  # лежит в исключённых кластерах — не трогаем

    @property
    def qty(self) -> int:
        return sum(line.qty for line in self.lines)


@dataclass
class Plan:
    candidates: list[Candidate]  # в порядке «хуже — первыми»; с учётом лимита — только вошедшие
    possible: int  # можно вывезти всего без лимита, шт.
    planned: int  # вывезти с учётом лимита, шт.
    left_out: int  # товаров, не вошедших в лимит
    no_sales_data: bool  # продажи неизвестны ни у одного товара — считать нечего


def kind_of(item: Item, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> str | None:
    """IDLE / SELLING — кандидат, None — нет (продажи неизвестны или запас не избыточный)."""
    if item.avg_daily is None or stock_status(item.available, item.avg_daily, thresholds) != EXCESS:
        return None
    return SELLING if item.avg_daily > 0 else IDLE


def surplus(item: Item, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> int | None:
    """Остаток сверх запаса на `excess` дней — у продающегося товара; у непродающегося ограничения нет (None)."""
    if item.avg_daily is None or item.avg_daily <= 0:
        return None
    need = math.ceil(item.avg_daily * thresholds.excess)
    return max(item.available - need, 0)


def _clusters(places: list[Place]) -> list[list[Place]]:
    groups: dict[str, list[Place]] = {}
    for p in places:
        groups.setdefault(_cluster_of(p), []).append(p)
    return sorted(groups.values(), key=lambda g: (-sum(p.present for p in g), g[0].cluster or "", g[0].warehouse))


def item_lines(places: list[Place], keep: int, cap: int | None, excluded: frozenset[str]) -> tuple[list[Line], int]:
    """Строки вывоза одного товара (без лимита «вывезти всего») и сколько штук лежит в исключённых кластерах."""
    lines: list[Line] = []
    excluded_units = 0
    remaining = cap
    for group in _clusters([p for p in places if p.present > 0]):
        total = sum(p.present for p in group)
        if group[0].cluster_key is not None and group[0].cluster_key in excluded:
            excluded_units += total
            continue
        budget = total - keep
        if remaining is not None:
            budget = min(budget, remaining)
        if budget <= 0:
            continue
        taken: list[tuple[Place, int]] = []
        for place in sorted(group, key=lambda p: (-p.present, p.warehouse)):
            qty = min(place.present, budget)
            if qty <= 0:
                break
            taken.append((place, qty))
            budget -= qty
        lines.extend(Line(place, qty, total) for place, qty in taken)
        if remaining is not None:
            remaining -= sum(q for _, q in taken)
    return settle(lines), excluded_units


def _order(c: Candidate) -> tuple:
    dws = c.item.days_without_sales
    days = c.days_of_supply
    return (
        -(_NEVER if dws is None else dws),
        -(Decimal(_NEVER) if days is None else days),  # не продаётся — запаса «бесконечно»
        -c.item.available,
        c.item.offer_id,
    )


def _limit(lines: list[Line], left: int) -> list[Line]:
    """Строки товара в пределах `left` шт. (последняя — частично)."""
    out: list[Line] = []
    for line in lines:
        if left <= 0:
            break
        qty = min(line.qty, left)
        out.append(Line(line.place, qty, line.cluster_total))
        left -= qty
    return settle(out)


def plan(items: list[Item], params: Params, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> Plan:
    candidates: list[Candidate] = []
    for item in items:
        kind = kind_of(item, thresholds)
        if kind is None:
            continue
        keep = params.keep_selling if kind == SELLING else params.keep_idle
        cap = surplus(item, thresholds)
        lines, excluded_units = item_lines(item.places, keep, cap, params.excluded)
        possible = sum(line.qty for line in lines)
        if possible <= 0:
            continue
        candidates.append(Candidate(
            item=item, kind=kind, keep=keep, surplus=cap, days_of_supply=days_of_supply(item.available, item.avg_daily),
            possible=possible, lines=lines, excluded_units=excluded_units,
        ))
    candidates.sort(key=_order)
    possible_total = sum(c.possible for c in candidates)
    left_out = 0
    if params.total is not None:
        left = params.total
        kept: list[Candidate] = []
        for c in candidates:
            if left <= 0:
                left_out += 1
                continue
            c.lines = _limit(c.lines, left)
            left -= c.qty
            kept.append(c)
        candidates = kept
    return Plan(
        candidates=candidates, possible=possible_total, planned=sum(c.qty for c in candidates), left_out=left_out,
        no_sales_data=all(i.avg_daily is None for i in items),
    )
