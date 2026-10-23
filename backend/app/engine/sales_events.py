"""События, которые влияли на продажи («Аналитика продаж», шаг 10.8). Чистый модуль — без БД и HTTP.

Источники — история, которую сервис уже хранит (правило 12): снимки цены пишутся при изменении (1.2), изменения через
сервис — `price_changes` (6.2–6.4), участие в акциях — вход и выход (4.3), снимки остатков по складам при изменении (1.3).
- **цена** — цена Ozon в снимке отличается от прошлой (0 и «не задана» — не цена); «через сервис» — если в этот же день
  сервис применил именно эту цену;
- **акция** — товар вошёл в акцию или вышел из неё (Ozon добавил сам — с пометкой);
- **нет остатка** — сумма остатка по всем складам стала 0; **снова в наличии** — была 0 и стала больше (пришла поставка).
  Остаток до периода неизвестен — первое значение только запоминаем, события нет.
События одного дня и вида собираются в одно: у одного товара — подробно, у нескольких — «у N товаров» со списком.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.engine.anomaly_text import plural, rub

PRICE, PROMO_IN, PROMO_OUT, STOCKOUT, RESTOCK = "price", "promo_in", "promo_out", "stockout", "restock"
KINDS = (PRICE, PROMO_IN, PROMO_OUT, STOCKOUT, RESTOCK)
MAX_ITEMS = 10  # товаров в одном событии — остальные «и ещё N»

SECTION = {  # куда вести, если товаров в событии несколько
    PRICE: "/dashboard/products/prices/history", PROMO_IN: "/dashboard/products/promotions",
    PROMO_OUT: "/dashboard/products/promotions", STOCKOUT: "/dashboard/warehouses", RESTOCK: "/dashboard/warehouses",
}


@dataclass(frozen=True)
class ProductRef:
    id: uuid.UUID
    offer_id: str
    name: str | None


@dataclass(frozen=True)
class Raw:
    """Событие одного товара."""

    day: date
    kind: str
    product: ProductRef
    text: str  # подробности для этого товара: «120 → 150 ₽, через сервис», «акция «Распродажа»»
    link: str


@dataclass
class Event:
    day: date
    kind: str
    title: str
    items: list[Raw] = field(default_factory=list)
    count: int = 0
    link: str = ""


# ── по одному товару ──
def price_changes(start: Decimal | None, points: list[tuple[date, Decimal | None]]) -> list[tuple[date, Decimal, Decimal]]:
    """(день, было, стало) — по снимкам по порядку; start — цена до периода. Пустая или нулевая цена — не цена: её
    пропускаем, сравнение — с последней настоящей."""
    out = []
    last = start if start and start > 0 else None
    for day, price in points:
        if not price or price <= 0:
            continue
        if last is not None and price != last:
            out.append((day, last, price))
        last = price
    return out


def stock_changes(start: dict[str, int], points: list[tuple[date, str, int]]) -> list[tuple[date, str]]:
    """(день, stockout | restock) — по снимкам остатка по складам по порядку; start — остатки складов до периода.
    Пока остаток хоть одного склада неизвестен до периода — первое значение только запоминаем."""
    stock = dict(start)
    total = sum(stock.values()) if stock else None
    out = []
    for day, warehouse, present in points:
        stock[warehouse] = max(present, 0)
        new_total = sum(stock.values())
        if total is not None:
            if total > 0 and new_total == 0:
                out.append((day, STOCKOUT))
            elif total == 0 and new_total > 0:
                out.append((day, RESTOCK))
        total = new_total
    return out


def price_raw(day: date, product: ProductRef, before: Decimal, after: Decimal, via_service: bool) -> Raw:
    arrow = "↑" if after > before else "↓"
    how = ", через сервис" if via_service else ""
    return Raw(day, PRICE, product, f"{arrow} {rub(before)} → {rub(after)}{how}",
               f"/dashboard/products/prices/history?product={product.id}")


def promo_raw(day: date, kind: str, product: ProductRef, promo_id: uuid.UUID, title: str, auto: bool) -> Raw:
    who = " — Ozon добавил сам" if auto and kind == PROMO_IN else ""
    return Raw(day, kind, product, f"«{title}»{who}", f"/dashboard/products/promotions/{promo_id}")


def stock_raw(day: date, kind: str, product: ProductRef) -> Raw:
    text = "остаток на всех складах — 0" if kind == STOCKOUT else "остаток снова больше нуля — пришла поставка"
    return Raw(day, kind, product, text, f"/dashboard/products?product={product.id}")


# ── по дням ──
def _title(kind: str, items: list[Raw]) -> str:
    n = len(items)
    if n == 1:
        r = items[0]
        return {
            PRICE: f"Цена {r.product.offer_id} {r.text}",
            PROMO_IN: f"{r.product.offer_id} вошёл в акцию {r.text}",
            PROMO_OUT: f"{r.product.offer_id} вышел из акции {r.text}",
            STOCKOUT: f"{r.product.offer_id} закончился на складах",
            RESTOCK: f"{r.product.offer_id} снова в наличии",
        }[kind]
    goods = f"{n} {plural(n, 'товар', 'товара', 'товаров')}"
    return {
        PRICE: f"Цена изменилась у {n} {plural(n, 'товара', 'товаров', 'товаров')}",  # у 1 товара, у 2 и 5 товаров
        PROMO_IN: f"В акции вошли {goods}",
        PROMO_OUT: f"Из акций вышли {goods}",
        STOCKOUT: f"Закончились {goods}",
        RESTOCK: f"Снова в наличии {goods}",
    }[kind]


def by_day(raw: list[Raw]) -> list[Event]:
    """События одного дня и вида — одним; по дням, внутри — в порядке KINDS."""
    groups: dict[tuple[date, str], list[Raw]] = {}
    for r in raw:
        groups.setdefault((r.day, r.kind), []).append(r)
    out = []
    for (day, kind), items in sorted(groups.items(), key=lambda kv: (kv[0][0], KINDS.index(kv[0][1]))):
        items.sort(key=lambda r: r.product.offer_id)
        out.append(Event(day, kind, _title(kind, items), items[:MAX_ITEMS], len(items),
                         items[0].link if len(items) == 1 else SECTION[kind]))
    return out
