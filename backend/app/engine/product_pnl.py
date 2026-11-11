"""Прибыль по товарам (шаг 3.4) — чистый расчёт без БД.

Строки начислений периода → по артикулу: продано, выручка, средняя цена, комиссия, логистика, возвраты, реклама,
хранение, прочие начисления, начислено Ozon, себестоимость, прибыль до налога, маржа.
- Строка с артикулом — его. Без артикула, но с номером отправления — артикулу этого отправления, если
  товар в отправлении один. Остальное (подписки, страхование, реклама по кампаниям, отправления с несколькими
  товарами) — строкой «Не распределено»: между товарами не делим, чтобы не исказить прибыль каждого.
- Хранение (шаг 4.2a): Ozon начисляет его одной строкой в день на весь магазин. Если за день есть отчёт Ozon
  «Стоимость размещения по товарам» — строка делится между товарами в долях этого отчёта (storage_shares); нет
  отчёта — «Не распределено», как раньше.
- Группы — как в юнит-экономике Ozon (шаг 4.2a): возвраты (обратная логистика, обработка возвратов, отмен и невыкупов)
  — отдельно от логистики; реклама и продвижение («Звёздные товары», оплата за заказ и за клик) и хранение — отдельно
  от прочего. «Возврат выручки» — по-прежнему в прочем: это не расход на возврат, а продажа, которую вернули.
- Выкуп (4.2a): отправление товара с обратной логистикой (или обработкой возврата), но без «Выручки» и «Возврата
  выручки» за период — невыкуп: товар доехал до покупателя и вернулся. Выкуп = продано / (продано + невыкуплено).
- Себестоимость — по строкам «Выручка» на дату строки, как в P&L периода; с НДС поставщика или без —
  по налоговому профилю (НДС с вычетом — без НДС: его возвращают вычетом). Нет себестоимости — прибыль
  и маржа None, а не «вся выручка — прибыль» (правило 5).
- Начислено Ozon по товарам + «Не распределено» = все начисления периода.
- Юнит-экономика (шаг 3.7a): те же суммы на 1 проданную штуку и доли от цены; штук нет — None.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.integrations.xlsx.common import norm

ZERO = Decimal(0)
CENT = Decimal("0.01")

# группы начислений по названию типа Ozon (названия меняются — сопоставляем по словам; norm: ё → е)
REVENUE = "выручка"
RETURN = "возврат выручки"
SALES_TYPES = ("выручка", "баллы за скидки", "программы партнеров")  # «Продажи» Ozon: по цене продавца
COMMISSION_WORDS = ("вознаграждени",)  # вознаграждение за продажу, возврат вознаграждения
RETURNS_WORDS = ("обратн", "невыкуп", "возврат")  # обратная логистика, обработка возвратов, отмен и невыкупов
# возврат выручки, баллов и партнёрских выплат — сторона продаж, а не расход на возврат; компенсации — доход
NOT_RETURNS_WORDS = ("выручк", "балл", "партнерск", "программ", "компенсац", "возмещени")
STORAGE_WORDS = ("хранени", "размещени")
ADS_WORDS = ("продвижени", "реклам", "трафарет", "оплата за клик", "оплата за заказ", "звездн", "отзыв")
LOGISTICS_WORDS = (
    "логистик", "доставк", "магистрал", "последняя миля", "обработк", "упаковк", "сборк", "выдач",
    "кросс-докинг", "кроссдокинг", "перевозк", "транзит",
)
GROUPS = ("revenue", "commission", "logistics", "returns", "ads", "storage", "other")
SHARE_NEAR_DAYS = 3  # нет долей хранения за день — берём ближайший день отчёта не дальше этого


def group_of(type_name: str) -> str:
    """revenue | commission | logistics | returns | ads | storage | other."""
    t = norm(type_name)
    if t == REVENUE:
        return "revenue"
    if any(w in t for w in COMMISSION_WORDS):
        return "commission"
    if any(w in t for w in RETURNS_WORDS) and not any(w in t for w in NOT_RETURNS_WORDS):
        return "returns"
    if any(w in t for w in STORAGE_WORDS):
        return "storage"
    if any(w in t for w in ADS_WORDS):
        return "ads"
    if any(w in t for w in LOGISTICS_WORDS):
        return "logistics"
    return "other"


def is_sales(type_name: str) -> bool:
    """Строка «Продаж» Ozon: выручка, баллы за скидки, программы партнёров — цена продавца по частям."""
    return norm(type_name) in SALES_TYPES


@dataclass
class LineIn:
    day: date
    type_name: str
    amount: Decimal
    quantity: int | None
    offer_id: str | None
    sku: str
    name: str
    posting_number: str | None


@dataclass
class ProductRow:
    offer_id: str
    name: str = ""
    sku: str = ""
    qty: int = 0  # продано: штуки строк «Выручка»
    returned_qty: int = 0  # штуки строк «Возврат выручки»
    not_bought: int = 0  # невыкуплено: отправления с обратной логистикой без выручки и возврата выручки
    revenue: Decimal = ZERO
    sales: Decimal = ZERO  # «Продажи» Ozon — выручка + баллы за скидки + программы партнёров (начисления в плюс)
    commission: Decimal = ZERO
    logistics: Decimal = ZERO
    returns: Decimal = ZERO  # обратная логистика, обработка возвратов, отмен и невыкупов
    ads: Decimal = ZERO  # реклама и продвижение с артикулом («Звёздные товары» и др.)
    storage: Decimal = ZERO  # хранение с артикулом и доля строки магазина по отчёту о размещении
    storage_by_report: Decimal = ZERO  # из него — по отчёту Ozon о размещении
    other: Decimal = ZERO
    total: Decimal = ZERO  # начислено Ozon по товару (все его строки)
    by_posting: Decimal = ZERO  # из них отнесено по номеру отправления
    cost: Decimal | None = None  # себестоимость проданного (с НДС или без — cost_basis), точная
    cost_missing: bool = False  # у товара нет себестоимости
    unit_cost: Decimal | None = None  # себестоимость штуки (средняя за период)
    by_type: dict[str, Decimal] = field(default_factory=dict)

    @property
    def avg_price(self) -> Decimal | None:
        return (self.revenue / self.qty).quantize(CENT) if self.qty else None

    @property
    def buyout_pct(self) -> Decimal | None:
        """Выкуп, %: из доехавших до покупателя — сколько купили. Ни продаж, ни невыкупов — None."""
        shipped = self.qty + self.not_bought
        return (Decimal(self.qty) / shipped * 100).quantize(Decimal("0.1"), ROUND_HALF_UP) if shipped else None

    @property
    def profit(self) -> Decimal | None:
        return None if self.cost is None else self.total - self.cost

    @property
    def margin(self) -> Decimal | None:
        profit = self.profit
        if profit is None or self.revenue <= 0:
            return None
        return (profit / self.revenue * 100).quantize(CENT)


UNIT_FIELDS = ("commission", "logistics", "returns", "ads", "storage", "other", "total", "cost", "profit")


def per_unit(row: ProductRow) -> dict[str, Decimal | None] | None:
    """Юнит-экономика: суммы товара на 1 проданную штуку и их доли от средней цены, %. Делим на проданные
    штуки (строки «Выручка»), как средняя цена; штук нет — None (не 0). Себестоимости нет — cost и profit None."""
    if row.qty <= 0:
        return None
    qty = Decimal(row.qty)
    out: dict[str, Decimal | None] = {"price": (row.revenue / qty).quantize(CENT)}
    for name in UNIT_FIELDS:
        value = getattr(row, name)
        out[name] = None if value is None else (value / qty).quantize(CENT)
        share = None if value is None or row.revenue <= 0 else (value / row.revenue * 100).quantize(CENT)
        out[f"{name}_pct"] = share
    return out


@dataclass
class Unallocated:
    total: Decimal = ZERO
    by_type: dict[str, Decimal] = field(default_factory=dict)
    by_group: dict[str, Decimal] = field(default_factory=dict)  # группа (реклама, хранение …) → сумма
    multi_product: Decimal = ZERO  # строки отправлений с несколькими товарами
    lines: int = 0


@dataclass
class ProductPnL:
    rows: list[ProductRow]
    unallocated: Unallocated
    period_total: Decimal  # все начисления периода
    revenue_lines_without_article: int


# (себестоимость единицы без НДС, коэффициент НДС поставщика у артикула или None) на дату
CostLookup = Callable[[str, date], tuple[Decimal, Decimal | None] | None]


def attribute(offer_id: str | None, posting_number: str | None,
              posting_offers: dict[str, set[str]]) -> tuple[str | None, bool, bool]:
    """Чья строка: (артикул или None — «Не распределено», отнесена по номеру отправления, отправление с несколькими
    товарами). Детализация «Аналитики прибыльности» (10.9) относит строки так же."""
    if offer_id:
        return offer_id, False, False
    if posting_number:
        offers = posting_offers.get(posting_number, set())
        if len(offers) == 1:
            return next(iter(offers)), True, False
        if len(offers) > 1:
            return None, False, True
    return None, False, False


StorageShares = dict[date, dict[str, Decimal]]  # день → артикул → вес (стоимость размещения по отчёту Ozon)


def shares_for(shares: StorageShares | None, day: date) -> dict[str, Decimal] | None:
    """Доли хранения на день: тот же день отчёта или ближайший не дальше SHARE_NEAR_DAYS (Ozon начисляет хранение за
    день, а отчёт может быть сдвинут на день)."""
    if not shares:
        return None
    for delta in range(SHARE_NEAR_DAYS + 1):
        for d in (day - timedelta(days=delta), day + timedelta(days=delta)):
            got = shares.get(d)
            if got and sum(got.values(), ZERO) > 0:
                return got
    return None


def split(amount: Decimal, weights: dict[str, Decimal]) -> list[tuple[str, Decimal]]:
    """Сумма по весам, до копейки; остаток округления — самой большой доле: части в сумме ровно amount."""
    positive = {k: w for k, w in weights.items() if w > 0}
    total = sum(positive.values(), ZERO)
    if total <= 0:
        return []
    order = sorted(positive, key=lambda k: (-positive[k], k))
    parts = {k: (amount * positive[k] / total).quantize(CENT, ROUND_HALF_UP) for k in order}
    parts[order[0]] += amount - sum(parts.values(), ZERO)
    return [(k, parts[k]) for k in order if parts[k] != 0]


@dataclass
class _Posting:
    sold: bool = False  # есть «Выручка»
    returned: bool = False  # есть «Возврат выручки»
    return_qty: int = 0  # штук в строках возврата (обратная логистика, обработка); 0 — строк нет


def compute(
    lines: list[LineIn],
    posting_offers: dict[str, set[str]],
    cost_of: CostLookup,
    with_vat: bool,
    default_vat: Decimal,
    storage_shares: StorageShares | None = None,
) -> ProductPnL:
    rows: dict[str, ProductRow] = {}
    unallocated = Unallocated()
    costs: dict[str, list[Decimal]] = {}  # артикул → [себестоимость в базе, штук с себестоимостью]
    postings: dict[str, dict[str, _Posting]] = {}  # артикул → отправление → что по нему было за период
    revenue_without_article = 0
    period_total = ZERO

    def add(row: ProductRow, ln: LineIn, group: str, amount: Decimal) -> None:
        row.total += amount
        row.by_type[ln.type_name] = row.by_type.get(ln.type_name, ZERO) + amount
        if group != "revenue":
            setattr(row, group, getattr(row, group) + amount)
        if is_sales(ln.type_name) and amount > 0:
            row.sales += amount

    for ln in lines:
        period_total += ln.amount
        group = group_of(ln.type_name)
        offer, via_posting, multi = attribute(ln.offer_id, ln.posting_number, posting_offers)
        if multi:
            unallocated.multi_product += ln.amount
        if not offer and group == "storage" and not ln.posting_number:
            weights = shares_for(storage_shares, ln.day)
            parts = split(ln.amount, weights) if weights else []
            for part_offer, part in parts:  # хранение магазина за день — по долям отчёта о размещении
                row = rows.setdefault(part_offer, ProductRow(part_offer))
                add(row, ln, group, part)
                row.storage_by_report += part
            if parts:
                continue
        if not offer:
            unallocated.total += ln.amount
            unallocated.lines += 1
            unallocated.by_type[ln.type_name] = unallocated.by_type.get(ln.type_name, ZERO) + ln.amount
            unallocated.by_group[group] = unallocated.by_group.get(group, ZERO) + ln.amount
            if group == "revenue":
                revenue_without_article += 1
            continue

        row = rows.setdefault(offer, ProductRow(offer))
        row.name = row.name or ln.name
        row.sku = row.sku or ln.sku
        add(row, ln, group, ln.amount)
        if via_posting:
            row.by_posting += ln.amount
        post = postings.setdefault(offer, {}).setdefault(ln.posting_number, _Posting()) if ln.posting_number else None
        if group == "revenue":
            qty = ln.quantity or 1  # как в отчёте: пусто или 0 — одна штука
            row.revenue += ln.amount
            row.qty += qty
            if post:
                post.sold = True
            rec = cost_of(offer, ln.day)
            if rec is None:
                row.cost_missing = True
            else:
                price, vat = rec
                cost = price * qty * ((vat if vat is not None else default_vat) if with_vat else 1)
                acc = costs.setdefault(offer, [ZERO, ZERO])
                acc[0] += cost
                acc[1] += qty
        elif norm(ln.type_name) == RETURN:
            row.returned_qty += ln.quantity or 1
            if post:
                post.returned = True
        elif group == "returns" and post:
            post.return_qty = max(post.return_qty, ln.quantity or 1)

    for offer, row in rows.items():
        row.not_bought = sum(p.return_qty for p in postings.get(offer, {}).values()
                             if p.return_qty and not p.sold and not p.returned)
        acc = costs.get(offer)
        if acc and not row.cost_missing:  # без округления: итоги считаются по точным суммам, округляет вывод
            row.cost = acc[0]
            row.unit_cost = acc[0] / acc[1] if acc[1] else None
        elif row.qty == 0 and not row.cost_missing:
            row.cost = ZERO  # продаж не было (только возвраты, хранение) — себестоимости нет
    return ProductPnL(list(rows.values()), unallocated, period_total, revenue_without_article)
