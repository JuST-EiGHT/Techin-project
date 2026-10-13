"""Интерфейс себестоимости для парсеров отчётов.

Парсеры знают только CostProvider. Откуда цифры (CSV сейчас, справочник в БД с шага 2.3) —
решает services/costs.py, парсеры при этом не меняются.
"""
from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol


class CostProvider(Protocol):
    def cost_of(self, article: str) -> Decimal | None:
        """Себестоимость единицы без НДС по артикулу; None — артикула нет в справочнике (не 0)."""


class MappingCostProvider:
    """Себестоимость из готового словаря «артикул → цена». Артикул сравнивается посимвольно."""

    def __init__(self, costs: Mapping[str, Decimal]):
        self._costs = dict(costs)

    def cost_of(self, article: str) -> Decimal | None:
        return self._costs.get(article)

    def __len__(self) -> int:
        return len(self._costs)
