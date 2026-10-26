"""Структура расходов («Аналитика прибыльности», шаг 10.9). Чистый модуль — без БД и HTTP.

Расходы периода = выручка − прибыль до налога P&L: все начисления Ozon, кроме «Выручки» (удержания — с минусом, доплаты
Ozon — с плюсом, уменьшают расходы), плюс себестоимость проданного (как в P&L: с НДС поставщика или без — по профилю), плюс
ручная поправка «сколько пришло» из отчётов, если она есть. Сумма групп = выручка − прибыль до налога — проверка в тестах.

Группы — по названию статьи (названия у Ozon меняются — сопоставляем по словам). Комиссии и логистика — ровно как колонки
«Прибыли по товарам» 3.4 (`engine/product_pnl.group_of`), её «Прочее» делится на рекламу и продвижение, хранение, возвраты
покупателей и прочее (эквайринг, подписки, страхование, баллы за скидки). Плюс себестоимость и поправка «сколько пришло».
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.engine import product_pnl
from app.integrations.xlsx.common import norm

ZERO = Decimal(0)
CENT = Decimal("0.01")

REVENUE, COMMISSION, LOGISTICS, ADS, STORAGE, RETURNS, COST, OTHER, ADJUSTMENT = (
    "revenue", "commission", "logistics", "ads", "storage", "returns", "cost", "other", "adjustment")
TITLES = {
    COMMISSION: "Комиссии Ozon", LOGISTICS: "Логистика", ADS: "Реклама и продвижение", STORAGE: "Хранение",
    RETURNS: "Возвраты покупателей", COST: "Себестоимость", OTHER: "Прочее", ADJUSTMENT: "Поправка «сколько пришло»",
}
ORDER = (COST, COMMISSION, LOGISTICS, ADS, STORAGE, RETURNS, OTHER, ADJUSTMENT)
ADS_WORDS = ("продвижени", "реклам", "трафарет", "оплата за клик", "оплата за заказ")
STORAGE_WORDS = ("хранени", "размещени")


def group_of(type_name: str) -> str:
    """Группа статьи начислений Ozon: выручка, комиссии и логистика — как в «Прибыли по товарам»; её «Прочее» — подробнее."""
    base = product_pnl.group_of(type_name)
    if base != "other":
        return {"revenue": REVENUE, "commission": COMMISSION, "logistics": LOGISTICS}[base]
    t = norm(type_name)
    if t == product_pnl.RETURN:
        return RETURNS
    if any(w in t for w in ADS_WORDS):
        return ADS
    if any(w in t for w in STORAGE_WORDS):
        return STORAGE
    return OTHER


@dataclass
class Group:
    key: str
    title: str
    amount: Decimal = ZERO  # расход, ₽: больше нуля — потратили; меньше — Ozon доплатил больше, чем удержал
    types: dict[str, Decimal] = field(default_factory=dict)  # статья → расход

    def share(self, revenue: Decimal) -> Decimal | None:
        return None if revenue <= 0 else (self.amount / revenue * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


@dataclass
class Structure:
    revenue: Decimal
    groups: list[Group]

    @property
    def expenses(self) -> Decimal:
        return sum((g.amount for g in self.groups), ZERO)

    @property
    def profit(self) -> Decimal:
        return self.revenue - self.expenses


def structure(by_type: dict[str, Decimal], cost: Decimal | None, adjustment: Decimal = ZERO) -> Structure:
    """by_type — начисления по статьям (как в P&L: + доход, − расход); cost — себестоимость проданного (None — не
    посчитать, группы нет); adjustment — сколько пришло сверх суммы по отчётам (ручная поправка)."""
    revenue = ZERO
    groups: dict[str, Group] = {}
    for name, amount in by_type.items():
        key = group_of(name)
        if key == REVENUE:
            revenue += amount
            continue
        g = groups.setdefault(key, Group(key, TITLES[key]))
        g.amount -= amount
        g.types[name] = g.types.get(name, ZERO) - amount
    if cost is not None:
        groups[COST] = Group(COST, TITLES[COST], cost)
    if adjustment:
        groups[ADJUSTMENT] = Group(ADJUSTMENT, TITLES[ADJUSTMENT], -adjustment)
    ordered = [groups[k] for k in ORDER if k in groups]
    for g in ordered:
        g.types = dict(sorted(g.types.items(), key=lambda kv: -kv[1]))
    return Structure(revenue, ordered)
