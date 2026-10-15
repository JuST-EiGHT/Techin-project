"""Прибыль по товарам (шаг 3.4) — чистый расчёт без БД.

Строки начислений периода → по артикулу: продано, выручка, средняя цена, комиссия, логистика, прочие
начисления, начислено Ozon, себестоимость, прибыль до налога, маржа.
- Строка с артикулом — его. Без артикула, но с номером отправления — артикулу этого отправления, если
  товар в отправлении один. Остальное (хранение, подписки, страхование, отправления с несколькими
  товарами) — строкой «Не распределено»: между товарами не делим, чтобы не исказить прибыль каждого.
- Себестоимость — по строкам «Выручка» на дату строки, как в P&L периода; с НДС поставщика или без —
  по налоговому профилю (НДС с вычетом — без НДС: его возвращают вычетом). Нет себестоимости — прибыль
  и маржа None, а не «вся выручка — прибыль» (правило 5).
- Начислено Ozon по товарам + «Не распределено» = все начисления периода.
- Юнит-экономика (шаг 3.7a): те же суммы на 1 проданную штуку и доли от цены; штук нет — None.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.integrations.xlsx.common import norm

ZERO = Decimal(0)
CENT = Decimal("0.01")

# группы начислений по названию типа Ozon (названия меняются — сопоставляем по словам)
REVENUE = "выручка"
RETURN = "возврат выручки"
COMMISSION_WORDS = ("вознаграждени",)  # вознаграждение за продажу, возврат вознаграждения
LOGISTICS_WORDS = (
    "логистик", "доставк", "магистрал", "последняя миля", "обработк", "упаковк", "сборк", "выдач",
    "кросс-докинг", "кроссдокинг", "перевозк", "транзит",
)


def group_of(type_name: str) -> str:
    """revenue | commission | logistics | other."""
    t = norm(type_name)
    if t == REVENUE:
        return "revenue"
    if any(w in t for w in COMMISSION_WORDS):
        return "commission"
    if any(w in t for w in LOGISTICS_WORDS):
        return "logistics"
    return "other"


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
    revenue: Decimal = ZERO
    commission: Decimal = ZERO
    logistics: Decimal = ZERO
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
    def profit(self) -> Decimal | None:
        return None if self.cost is None else self.total - self.cost

    @property
    def margin(self) -> Decimal | None:
        profit = self.profit
        if profit is None or self.revenue <= 0:
            return None
        return (profit / self.revenue * 100).quantize(CENT)


UNIT_FIELDS = ("commission", "logistics", "other", "total", "cost", "profit")


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


def compute(
    lines: list[LineIn],
    posting_offers: dict[str, set[str]],
    cost_of: CostLookup,
    with_vat: bool,
    default_vat: Decimal,
) -> ProductPnL:
    rows: dict[str, ProductRow] = {}
    unallocated = Unallocated()
    costs: dict[str, list[Decimal]] = {}  # артикул → [себестоимость в базе, штук с себестоимостью]
    revenue_without_article = 0
    period_total = ZERO

    for ln in lines:
        period_total += ln.amount
        offer, via_posting = ln.offer_id, False
        if not offer and ln.posting_number:
            offers = posting_offers.get(ln.posting_number, set())
            if len(offers) == 1:
                offer, via_posting = next(iter(offers)), True
            elif len(offers) > 1:
                unallocated.multi_product += ln.amount
        if not offer:
            unallocated.total += ln.amount
            unallocated.lines += 1
            unallocated.by_type[ln.type_name] = unallocated.by_type.get(ln.type_name, ZERO) + ln.amount
            if group_of(ln.type_name) == "revenue":
                revenue_without_article += 1
            continue

        row = rows.setdefault(offer, ProductRow(offer))
        row.name = row.name or ln.name
        row.sku = row.sku or ln.sku
        row.total += ln.amount
        row.by_type[ln.type_name] = row.by_type.get(ln.type_name, ZERO) + ln.amount
        if via_posting:
            row.by_posting += ln.amount
        group = group_of(ln.type_name)
        if group == "revenue":
            qty = ln.quantity or 1  # как в отчёте: пусто или 0 — одна штука
            row.revenue += ln.amount
            row.qty += qty
            rec = cost_of(offer, ln.day)
            if rec is None:
                row.cost_missing = True
            else:
                price, vat = rec
                cost = price * qty * ((vat if vat is not None else default_vat) if with_vat else 1)
                acc = costs.setdefault(offer, [ZERO, ZERO])
                acc[0] += cost
                acc[1] += qty
        else:
            setattr(row, group, getattr(row, group) + ln.amount)
            if norm(ln.type_name) == RETURN:
                row.returned_qty += ln.quantity or 1

    for offer, row in rows.items():
        acc = costs.get(offer)
        if acc and not row.cost_missing:  # без округления: итоги считаются по точным суммам, округляет вывод
            row.cost = acc[0]
            row.unit_cost = acc[0] / acc[1] if acc[1] else None
        elif row.qty == 0 and not row.cost_missing:
            row.cost = ZERO  # продаж не было (только возвраты, хранение по отправлению) — себестоимости нет
    return ProductPnL(list(rows.values()), unallocated, period_total, revenue_without_article)
