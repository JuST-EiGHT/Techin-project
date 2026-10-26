"""P&L как отчёт о прибылях и убытках (шаг 10.22). Чистый модуль — без БД и HTTP.

Строки сверху вниз:
    выручка → возвраты покупателей → корректировки и доплаты Ozon (баллы за скидки, компенсации, выплаты партнёров)
    → комиссии, логистика, хранение, реклама и продвижение, прочие услуги Ozon → поправка «сколько пришло» (если есть)
    = пришло от Ozon → себестоимость проданного (с НДС поставщика, как в P&L)
    = прибыль от продаж на Ozon до налога — ровно P&L 3.1
    → прочие расходы бизнеса по статьям «в P&L» (10.19; только при включённом «Планировании»)
    = прибыль до налога → налог и НДС по налоговому профилю (расходы бизнеса уменьшают базу у режимов «от прибыли»)
    = чистая прибыль и рентабельность (чистая прибыль / выручка).
Строки Ozon — по названию статьи начислений, как группы «Аналитики прибыльности» (`engine/profit_structure`); их сумма —
ровно «пришло от Ozon». Без расходов бизнеса налог, остаток и маржа — те же, что в P&L 3.1 (эталоны не меняются).
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.engine import profit_structure as ps

ZERO = Decimal(0)
CENT = Decimal("0.01")

REVENUE, RETURNS, ADJUSTMENTS = "revenue", "returns", "adjustments"
COMMISSION, LOGISTICS, STORAGE, ADS, OTHER, MANUAL = "commission", "logistics", "storage", "ads", "other", "manual"
OZON_TOTAL, COST, PROFIT_OZON = "ozon_total", "cost", "profit_ozon"
BUSINESS, PROFIT, TAX, VAT, NET = "business", "profit_before_tax", "tax", "vat", "net"

TITLES = {
    REVENUE: "Выручка",
    RETURNS: "Возвраты покупателей",
    ADJUSTMENTS: "Корректировки и доплаты Ozon",
    COMMISSION: "Комиссии Ozon",
    LOGISTICS: "Логистика",
    STORAGE: "Хранение",
    ADS: "Реклама и продвижение",
    OTHER: "Прочие услуги Ozon",
    MANUAL: "Поправка «сколько пришло»",
    OZON_TOTAL: "Пришло от Ozon",
    PROFIT_OZON: "Прибыль от продаж на Ozon",
    BUSINESS: "Прочие расходы бизнеса",
    PROFIT: "Прибыль до налога",
    VAT: "НДС к уплате",
    NET: "Чистая прибыль",
}
OZON_ORDER = (REVENUE, RETURNS, ADJUSTMENTS, COMMISSION, LOGISTICS, STORAGE, ADS, OTHER)
_FROM_STRUCTURE = {
    ps.REVENUE: REVENUE, ps.RETURNS: RETURNS, ps.COMMISSION: COMMISSION, ps.LOGISTICS: LOGISTICS,
    ps.STORAGE: STORAGE, ps.ADS: ADS,
}


def line_of(type_name: str, amount: Decimal) -> str:
    """Строка P&L для статьи начислений Ozon. Из «прочего» доплаты Ozon (баллы за скидки, компенсации, выплаты
    партнёров — сумма за период в плюс) — корректировки, удержания — прочие услуги Ozon."""
    key = ps.group_of(type_name)
    if key == ps.OTHER:
        return ADJUSTMENTS if amount > 0 else OTHER
    return _FROM_STRUCTURE[key]


@dataclass
class Category:
    """Статья расходов бизнеса: сколько потрачено за период (больше нуля — расход; возвраты денег его уменьшают)."""

    id: str
    name: str
    amount: Decimal
    children: list["Category"] = field(default_factory=list)


@dataclass
class Row:
    key: str
    title: str
    amount: Decimal | None  # со знаком: + доход, − расход; итог — как есть; None — не посчитать (налог не задан)
    kind: str  # line — строка, subtotal — промежуточный итог, total — чистая прибыль
    types: list[tuple[str, Decimal]] = field(default_factory=list)  # статьи начислений Ozon в строке
    children: list[Category] = field(default_factory=list)  # статьи расходов бизнеса

    def pct(self, revenue: Decimal) -> Decimal | None:
        if self.amount is None or revenue <= 0:
            return None
        return (self.amount / revenue * 100).quantize(CENT, ROUND_HALF_UP)


@dataclass
class Statement:
    rows: list[Row]
    revenue: Decimal  # «Выручка» — база для процентов и рентабельности
    profit_ozon: Decimal  # прибыль от продаж на Ozon до налога — P&L 3.1
    business: Decimal | None  # расходы бизнеса за период; None — не учитываются (планирование выключено)
    profit: Decimal  # прибыль до налога — с расходами бизнеса
    tax: Decimal | None
    vat: Decimal | None
    net: Decimal | None  # чистая прибыль; None — налог не задан

    @property
    def margin(self) -> Decimal | None:
        """Рентабельность: чистая прибыль / выручка × 100."""
        if self.net is None or self.revenue <= 0:
            return None
        return (self.net / self.revenue * 100).quantize(CENT, ROUND_HALF_UP)


def ozon_rows(by_type: dict[str, Decimal]) -> list[Row]:
    """Начисления Ozon по статьям → строки P&L в порядке отчёта; пустых строк нет."""
    rows: dict[str, Row] = {}
    for name, amount in by_type.items():
        amount = Decimal(amount)
        key = line_of(name, amount)
        row = rows.setdefault(key, Row(key, TITLES[key], ZERO, "line"))
        row.amount += amount
        row.types.append((name, amount))
    out = [rows[k] for k in OZON_ORDER if k in rows]
    for r in out:
        r.types.sort(key=lambda t: -abs(t[1]))
    return out


def build(
    by_type: dict[str, Decimal],
    revenue_base: Decimal,
    auto_total: Decimal,
    effective_total: Decimal,
    owed: Decimal,
    cost_title: str,
    business: list[Category] | None,
    tax: Decimal | None,
    vat: Decimal | None,
    tax_title: str | None,
    net_ozon: Decimal | None = None,
) -> Statement:
    """Отчёт о прибылях и убытках. revenue_base — выручка P&L (база процентов); owed — себестоимость с НДС (как в
    P&L); business — статьи расходов бизнеса или None, если они не учитываются; tax и vat — уже посчитанные с этими
    расходами (None — налог не задан). net_ozon — остаток P&L 3.1: без расходов бизнеса чистая прибыль — ровно он
    (P&L округляет один раз, в конце)."""
    rows = ozon_rows(by_type)
    if effective_total != auto_total:
        rows.append(Row(MANUAL, TITLES[MANUAL], effective_total - auto_total, "line"))
    rows.append(Row(OZON_TOTAL, TITLES[OZON_TOTAL], effective_total, "subtotal"))
    rows.append(Row(COST, cost_title, -owed, "line"))
    profit_ozon = effective_total - owed
    rows.append(Row(PROFIT_OZON, TITLES[PROFIT_OZON], profit_ozon, "subtotal"))
    spent = None
    profit = profit_ozon
    if business is not None:
        spent = sum((c.amount for c in business), ZERO)
        rows.append(Row(BUSINESS, TITLES[BUSINESS], -spent, "line", children=business))
        profit = profit_ozon - spent
        rows.append(Row(PROFIT, TITLES[PROFIT], profit, "subtotal"))
    rows.append(Row(TAX, f"Налог · {tax_title}" if tax_title else "Налог", None if tax is None else -tax, "line"))
    if vat:
        rows.append(Row(VAT, TITLES[VAT], -vat, "line"))
    net = None
    if tax is not None:
        net = net_ozon if not spent and net_ozon is not None else profit - tax - (vat or ZERO)
    rows.append(Row(NET, TITLES[NET], net, "total"))
    return Statement(rows, revenue_base, profit_ozon, spent, profit, tax, vat, net)
