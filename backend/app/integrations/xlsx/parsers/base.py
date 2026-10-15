"""Общее для парсеров: результат разбора, учёт продаж по артикулам и себестоимости."""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.integrations.xlsx.costs import CostProvider
from app.schemas.report import ArticleSales, MissingCost, NormalizedReportData

CENT = Decimal("0.01")
MISSING_COST_WARNING = "Не найдена себестоимость для"  # начало предупреждения — его пересобирают при смене цен


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


@dataclass
class AccrualLine:
    """Строка отчёта по начислениям как есть — для сохранения в accruals (source='xlsx')."""

    row_no: int  # строка в Excel (с 1)
    accrual_date: date | None
    type_name: str
    posting_number: str  # «ID начисления»: номер отправления, ID рекламной кампании или пусто
    offer_id: str
    sku: int | None
    quantity: int | None
    amount: Decimal
    raw: dict[str, str]  # все колонки строки: заголовок → текст ячейки


@dataclass
class ParsedReport:
    data: NormalizedReportData
    accrual_lines: list[AccrualLine] = field(default_factory=list)  # только у отчёта по начислениям


class SalesLedger:
    """Строки с товарами → by_article, missing_cost и себестоимость проданного.

    Неизвестная себестоимость не превращается в 0 (правило 5): товар попадает в missing_cost,
    а в cost_sales не входит — об этом предупреждение, прибыль будет завышена.
    """

    def __init__(self, costs: CostProvider):
        self._costs = costs
        self.cost_sales = Decimal(0)
        self.rows = 0
        self.rows_without_article = 0
        self._articles: dict[str, dict] = {}
        self._missing: dict[str, dict] = {}

    def add(self, article: str, sku: str, name: str, qty: int, revenue: Decimal) -> None:
        self.rows += 1
        if not article:
            self.rows_without_article += 1
            return
        cost = self._costs.cost_of(article)
        if cost is None:
            missing = self._missing.setdefault(article, {"article": article, "sku": sku, "name": name, "qty": 0})
            missing["qty"] += qty
        else:
            self.cost_sales += cost * qty
        entry = self._articles.setdefault(
            article, {"name": name, "sku": sku, "qty": 0, "revenue": Decimal(0), "cost": cost}
        )
        entry["qty"] += qty
        entry["revenue"] += revenue

    @property
    def revenue(self) -> Decimal:
        return sum((a["revenue"] for a in self._articles.values()), Decimal(0))

    def by_article(self) -> dict[str, ArticleSales]:
        out = {}
        for article, a in self._articles.items():
            cost = a["cost"]
            out[article] = ArticleSales(
                name=a["name"], sku=a["sku"], qty=a["qty"], revenue=money(a["revenue"]), cost=cost,
                total_cost=money(cost * a["qty"]) if cost is not None else None, has_cost=cost is not None,
            )
        return out

    def missing_cost(self) -> list[MissingCost]:
        return [MissingCost(**m) for m in self._missing.values()]

    def warnings(self, row_kind: str) -> list[str]:
        out = []
        if self._missing:
            qty = sum(m["qty"] for m in self._missing.values())
            out.append(
                f"{MISSING_COST_WARNING} {len(self._missing)} артикулов ({qty} шт.) — долг поставщику "
                "занижен, прибыль завышена. Добавьте их себестоимость в справочник."
            )
        if self.rows_without_article:
            out.append(
                f"Строк {row_kind} без артикула: {self.rows_without_article} — себестоимость по ним не учтена, "
                "прибыль завышена."
            )
        return out
