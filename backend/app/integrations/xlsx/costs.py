"""Интерфейс себестоимости для парсеров отчётов.

Парсеры знают только CostProvider. Откуда цифры (справочник в БД с историей —
services/costs.py), решает вызывающий код, парсеры при этом не меняются.
"""
from collections.abc import Mapping
from datetime import date
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

    def at(self, day: date) -> "MappingCostProvider":
        return self  # без истории — цена одна на любую дату

    def __len__(self) -> int:
        return len(self._costs)


def costs_on(costs: CostProvider, day: date) -> CostProvider:
    """Себестоимость на дату, если источник знает историю цен (метод at), иначе как есть."""
    at = getattr(costs, "at", None)
    return at(day) if callable(at) else costs
