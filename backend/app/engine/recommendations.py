"""Рекомендуемые действия (шаг 10.6, BUSINESS_LOGIC §10). Чистый модуль — без БД и HTTP.

Правило → условие → «почему» с цифрами → на каком расчёте основано → действие (ссылка). Расчёты — готовые, здесь только
правила и тексты: калькулятор цены 4.2 (минимальная цена, маржа в акции), поставки 5.2 (срочность по кластерам),
кандидаты на вывоз 5.1, продажи без себестоимости (справочник 2.3 и начисления 3.5).

Данных мало — рекомендации нет, а не «примерно»: продаж с данными меньше 14 дней из 30 — ни одной; у товара нет цены
или себестоимости — по нему ни одной, кроме «заполнить себестоимость»; поставкам нужны ещё 14 дней отправлений.

Порядок: акция в минус → цена ниже минимальной → поставка «Срочно» → «Скоро» → себестоимость → вывоз; внутри — больше
денег на кону (убыток за 30 дней или до конца акции, заморожено в товаре), у поставок — меньше дней запаса, затем по
артикулу. «Сделано» отдельно не отмечается: условие ушло — рекомендация исчезает сама.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.engine.anomaly_detector import CostGap
from app.engine.anomaly_text import num, plural, rub

PROMO, PRICE, SUPPLY, COST, WITHDRAW = "promo_loss", "price_below_min", "supply", "missing_cost", "withdraw"
KINDS = (PROMO, PRICE, SUPPLY, COST, WITHDRAW)
MIN_DATA_DAYS = 14  # продаж с данными за 30 дней — меньше не рекомендуем
WINDOW_DAYS = 30
RISK_DAYS = 30  # убыток при текущей цене — за 30 дней; в акции — до её конца, но не дольше
TOP = 3  # сколько товаров назвать в сводной рекомендации
CRITICAL, URGENT = "critical", "urgent"  # срочность поставки 5.2: «Срочно», «Скоро»
SUPPLY_KIND, PURCHASE_KIND = "supply", "purchase"  # поставка в кластер Ozon / закупка на свой склад
_KOP = Decimal("0.01")
# акция в минус → цена ниже минимальной → поставка «Срочно» → «Скоро» → себестоимость → вывоз
RANK = {PROMO: 0, PRICE: 1, SUPPLY: 2, COST: 4, WITHDRAW: 5}

# на каком расчёте основано — названия страниц, где его видно (не номера шагов)
BASIS = {
    PROMO: "калькулятор цены («Цены») при цене акции: себестоимость с НДС, комиссия и эквайринг Ozon, логистика по "
           "начислениям, налог по профилю, реклама.",
    PRICE: "калькулятор минимальной цены («Цены»): ниже неё продажа в убыток — учитывает себестоимость с НДС, комиссию и "
           "эквайринг Ozon, логистику по начислениям, налог по профилю, рекламу.",
    COST: "справочник «Себестоимость» на день продажи и продажи по начислениям Ozon — как в P&L.",
}


@dataclass(frozen=True)
class ProductRef:
    id: uuid.UUID
    offer_id: str
    name: str | None


@dataclass(frozen=True)
class PromoData:
    """Самая низкая цена товара в текущих акциях — самая низкая маржа (таблица цен 4.2)."""

    id: uuid.UUID
    title: str
    action_price: Decimal
    margin: Decimal | None
    date_to: date | None = None
    auto: bool = False  # Ozon добавил сам


@dataclass(frozen=True)
class ProductData:
    product: ProductRef
    price: Decimal | None  # текущая цена Ozon; нет — None
    has_cost: bool  # себестоимость на сегодня есть в справочнике
    avg_daily: Decimal | None = None  # продаж в день за 30 дней; нет данных — None
    min_price: Decimal | None = None  # калькулятор 4.2
    target_price: Decimal | None = None
    margin: Decimal | None = None  # при текущей цене, %
    unit_profit: Decimal | None = None  # остаётся с 1 шт. при текущей цене
    promo: PromoData | None = None

    @property
    def usable(self) -> bool:
        """Есть цена и себестоимость — по товару можно рекомендовать."""
        return self.price is not None and self.has_cost


@dataclass(frozen=True)
class SupplyLine:
    """Строка «Поставок» 5.2: товар × куда везти."""

    product_id: uuid.UUID
    kind: str  # supply | purchase
    urgency: str  # critical | urgent | planned
    destination: str
    to_order: int
    days_left: Decimal | None
    avg_daily: Decimal
    enough: bool = True  # отправлений с данными за 30 дней — не меньше 14


@dataclass(frozen=True)
class IdleStock:
    """Кандидат на вывоз 5.1 без продаж за 30 дней."""

    product_id: uuid.UUID
    available: int
    qty: int  # к вывозу
    days_without_sales: int | None  # None — продаж в данных нет
    cost_with_vat: Decimal | None


@dataclass(frozen=True)
class Inputs:
    today: date
    data_days: int  # дней с данными о продажах (начисления) за последние 30
    products: dict[uuid.UUID, ProductData] = field(default_factory=dict)
    supply: list[SupplyLine] = field(default_factory=list)
    idle: list[IdleStock] = field(default_factory=list)
    cost_gaps: list[CostGap] = field(default_factory=list)
    target_days: int = 45  # поставки: запас после поставки
    lead_time_days: int = 7  # поставки: дней в пути
    keep_idle: int = 2  # вывоз: оставляем в кластере у товара без продаж


@dataclass(frozen=True)
class Recommendation:
    kind: str
    key: str  # что скрывают: правило и предмет (товар, товар × акция)
    title: str  # что сделать
    why: str  # почему — условие и цифры
    basis: str  # на каком расчёте основано
    action: str  # подпись кнопки
    link: str
    product: ProductRef | None = None
    money: Decimal | None = None  # сколько на кону, ₽ — где можно оценить
    money_label: str | None = None  # «убыток за 30 дней», «заморожено в товаре» …
    weight: Decimal = Decimal(0)  # внутри правила — больше первыми
    rank: int = 0  # порядок правил: RANK


def enough_data(inp: Inputs) -> bool:
    return inp.data_days >= MIN_DATA_DAYS


def _money(v: Decimal | None) -> Decimal | None:
    return Decimal(v).quantize(_KOP, ROUND_HALF_UP) if v is not None and v > 0 else None


def _who(p: ProductRef) -> str:
    return f"{p.offer_id}{f' «{p.name}»' if p.name else ''}"


def _per_day(avg: Decimal | None) -> str:
    return f"продаётся {num(avg, 2)} шт. в день" if avg else "продаж за 30 дней нет"


# ── правила ──
def promo_loss(d: ProductData, today: date) -> Recommendation | None:
    """Акция в минус: маржа при цене акции ниже нуля — выйти из акции или поднять цену."""
    pr = d.promo
    if pr is None or pr.margin is None or pr.margin >= 0:
        return None
    loss_unit = -pr.margin / 100 * pr.action_price
    days = RISK_DAYS if pr.date_to is None else min(max((pr.date_to - today).days + 1, 1), RISK_DAYS)
    money = _money(loss_unit * d.avg_daily * days) if d.avg_daily else None
    who = "Ozon добавил товар сам. " if pr.auto else ""
    end = f"акция идёт ещё {days} {plural(days, 'день', 'дня', 'дней')}" if pr.date_to else "конец акции не указан"
    why = (f"{who}В акции по цене {rub(pr.action_price)} маржа {num(pr.margin)} % — с каждой продажи минус около "
           f"{rub(loss_unit)}; {_per_day(d.avg_daily)}, {end}.")
    return Recommendation(
        PROMO, f"promo:{d.product.id}:{pr.id}", f"Выйти из акции «{pr.title}» или поднять цену {d.product.offer_id}",
        why, BASIS[PROMO], "Открыть акцию", f"/dashboard/products/promotions/{pr.id}", d.product,
        money, "убыток до конца акции" if pr.date_to else "убыток за 30 дней", money or Decimal(0), RANK[PROMO],
    )


def price_below_min(d: ProductData) -> Recommendation | None:
    """Цена ниже минимальной по калькулятору — каждая продажа в минус: проверить цену."""
    if d.min_price is None or d.price is None or d.price >= d.min_price:
        return None
    pct = ((d.min_price - d.price) / d.price * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    loss_unit = -d.unit_profit if d.unit_profit is not None and d.unit_profit < 0 else None
    money = _money(loss_unit * d.avg_daily * RISK_DAYS) if loss_unit and d.avg_daily else None
    margin = f" — маржа {num(d.margin)} %" if d.margin is not None else ""
    loss = f", с каждой продажи минус {rub(loss_unit)}" if loss_unit else ""
    target = f", целевая — {rub(d.target_price)}" if d.target_price is not None else ""
    why = (f"Цена {rub(d.price)} ниже минимальной {rub(d.min_price)}{margin}{loss}; {_per_day(d.avg_daily)}. "
           f"Без убытка — от {rub(d.min_price)} (+{num(pct)} %){target}.")
    return Recommendation(
        PRICE, f"price:{d.product.id}", f"Проверить цену {d.product.offer_id}", why, BASIS[PRICE], "Проверить цену",
        f"/dashboard/products/prices?product={d.product.id}", d.product, money, "убыток за 30 дней",
        money or Decimal(0), RANK[PRICE],
    )


def supply(d: ProductData, lines: list[SupplyLine], inp: Inputs) -> Recommendation | None:
    """Поставка «Срочно» или «Скоро» (5.2) — запланировать поставку (закупку). Одна рекомендация на товар."""
    hot = sorted((x for x in lines if x.urgency in (CRITICAL, URGENT) and x.enough and x.to_order > 0),
                 key=lambda x: (x.urgency != CRITICAL, x.days_left if x.days_left is not None else Decimal(10**6)))
    if not hot:
        return None
    first = hot[0]
    critical = first.urgency == CRITICAL
    label = "Срочно" if critical else "Скоро"
    units = sum(x.to_order for x in hot)
    places = len(hot)
    where = (f"в кластере «{first.destination}»" if first.kind == SUPPLY_KIND else f"на складе «{first.destination}»")
    left = f"хватит на {num(first.days_left)} дн." if first.days_left is not None else "остатка нет"
    only_purchase = all(x.kind == PURCHASE_KIND for x in hot)
    verb = "закупить" if only_purchase else "отправить"
    why = (f"{label}: {where} {left} при продажах {num(first.avg_daily, 2)} шт. в день. Всего {verb} {units} шт. — "
           f"{places} {plural(places, 'направление', 'направления', 'направлений')}.")
    basis = (f"«Поставки» по продажам за 30 и 7 дней по кластерам покупателей; запас после поставки {inp.target_days} дн. "
             f"и {inp.lead_time_days} дн. в пути — «Настройки»; «Срочно» и «Скоро» — пороги остатков «Критично» и «Мало».")
    title = f"{'Закупить' if only_purchase else 'Запланировать поставку'} {d.product.offer_id}"
    days = first.days_left if first.days_left is not None else Decimal(0)
    return Recommendation(
        SUPPLY, f"supply:{d.product.id}", title, why, basis, "К поставкам", "/dashboard/warehouses/supply", d.product,
        weight=-days, rank=RANK[SUPPLY] if critical else RANK[SUPPLY] + 1,  # меньше дней запаса — выше
    )


def missing_cost(gaps: list[CostGap], product: ProductRef | None = None) -> Recommendation | None:
    """Продажи без себестоимости — прибыль, минимальная цена и маржа по ним не считаются: заполнить себестоимость."""
    if not gaps:
        return None
    revenue = sum((g.revenue for g in gaps), Decimal(0))
    n = len(gaps)
    if product is not None or n == 1:
        g = gaps[0]
        who = product.offer_id if product is not None else g.offer_id
        title = f"Заполнить себестоимость {who}"
        why = (f"За 30 дней продано {g.qty} шт. на {rub(revenue)}, а себестоимости нет — прибыль, минимальная цена и маржа "
               "по товару не считаются (это не ноль).")
        key = f"cost:{product.id}" if product is not None else "costs"
        link = f"/dashboard/products/costs?missing=1&search={who}" if product is not None else "/dashboard/products/costs?missing=1"
    else:
        top = sorted(gaps, key=lambda g: (-g.revenue, g.offer_id))[:TOP]
        names = ", ".join(f"{g.offer_id} ({rub(g.revenue)})" for g in top)
        more = f" и ещё {n - len(top)}" if n > len(top) else ""
        title = f"Заполнить себестоимость: {n} {plural(n, 'товар', 'товара', 'товаров')}"
        why = (f"За 30 дней продано на {rub(revenue)} без себестоимости — прибыль, минимальная цена и маржа по этим "
               f"товарам не считаются (это не ноль). Больше всего: {names}{more}. Можно шаблоном Excel.")
        key, link = "costs", "/dashboard/products/costs?missing=1"
    return Recommendation(COST, key, title, why, BASIS[COST], "Заполнить", link, product, weight=revenue, rank=RANK[COST])


def withdraw(d: ProductData, idle: IdleStock, inp: Inputs) -> Recommendation | None:
    """Избыток при долгом отсутствии продаж (кандидат на вывоз 5.1 без продаж за 30 дней) — вывезти."""
    if idle.qty <= 0:
        return None
    money = _money(idle.cost_with_vat * idle.qty) if idle.cost_with_vat is not None else None
    since = (f"Продаж нет {idle.days_without_sales} дн." if idle.days_without_sales is not None
             else "Продаж в данных нет.")
    frozen = f" на {rub(money)} по себестоимости — деньги заморожены, а Ozon берёт за хранение." if money else ""
    why = f"{since} На складах {idle.available} шт. — к вывозу {idle.qty} шт.{frozen}"
    basis = (f"«На вывоз» — статус «Избыток» и продаж за 30 дней нет; в каждом кластере оставляем {inp.keep_idle} шт. "
             "(параметры — там же).")
    return Recommendation(
        WITHDRAW, f"withdraw:{d.product.id}", f"Вывезти {d.product.offer_id} со складов Ozon", why, basis, "На вывоз",
        "/dashboard/warehouses/withdrawal", d.product, money, "заморожено в товаре", money or Decimal(0),
        RANK[WITHDRAW],
    )


def recommend(inp: Inputs, product_id: uuid.UUID | None = None) -> list[Recommendation]:
    """Все рекомендации магазина (или одного товара — для «Статистики товара», 10.14) по порядку."""
    if not enough_data(inp):
        return []
    products = {k: v for k, v in inp.products.items() if product_id is None or k == product_id}
    out: list[Recommendation] = []
    by_product: dict[uuid.UUID, list[SupplyLine]] = {}
    for line in inp.supply:
        by_product.setdefault(line.product_id, []).append(line)
    idle = {x.product_id: x for x in inp.idle}
    for pid, d in products.items():
        if not d.usable:
            continue
        for rec in (promo_loss(d, inp.today), price_below_min(d),
                    supply(d, by_product.get(pid, []), inp),
                    withdraw(d, idle[pid], inp) if pid in idle else None):
            if rec is not None:
                out.append(rec)
    if product_id is None:
        rec = missing_cost(inp.cost_gaps)
    else:
        d = inp.products.get(product_id)
        mine = [g for g in inp.cost_gaps if d is not None and g.offer_id == d.product.offer_id]
        rec = missing_cost(mine, d.product) if d is not None else None
    if rec is not None:
        out.append(rec)
    return sorted(out, key=_order)


def _order(r: Recommendation):
    return r.rank, -r.weight, r.product.offer_id if r.product else ""
