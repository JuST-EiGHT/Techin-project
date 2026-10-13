"""Себестоимость товаров для расчётов.

Справочника себестоимости в БД пока нет — он появится на шаге 2.3. До тех пор:
- cost_prices (по товарам из БД) пуст, и расчёты, которым нужна себестоимость (маржа в акции),
  возвращают None, а не 0 (правило 5: неизвестная себестоимость ≠ 0);
- разбор XLSX-отчётов берёт себестоимость по артикулу из reference/cost_prices.csv через
  get_cost_provider. На шаге 2.3 меняется только эта функция — парсеры знают лишь CostProvider.
"""
import csv
import logging
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.xlsx.costs import CostProvider, MappingCostProvider

log = logging.getLogger(__name__)


async def cost_prices(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """product_id → себестоимость единицы без НДС."""
    return {}


def load_cost_csv(path: str | Path) -> MappingCostProvider:
    """CSV с колонками offer_id, cost_price (без НДС). Нет файла или битая строка — в лог, не исключение:
    артикулы без себестоимости парсер покажет пользователю в missing_cost."""
    costs: dict[str, Decimal] = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for line_no, row in enumerate(csv.DictReader(f), start=2):
                article = (row.get("offer_id") or "").strip()
                try:
                    cost = Decimal((row.get("cost_price") or "").strip().replace(",", "."))
                except InvalidOperation:
                    cost = None
                if not article or cost is None or not cost.is_finite() or cost < 0:
                    log.warning("Себестоимость: пропущена строка %s файла %s", line_no, path)
                    continue
                costs[article] = cost
    except OSError:
        log.warning("Файл себестоимости %s не найден — себестоимость неизвестна для всех товаров", path)
    return MappingCostProvider(costs)


def get_cost_provider() -> CostProvider:
    """Зависимость FastAPI: себестоимость для разбора отчётов. Файл маленький — читается при каждом запросе."""
    return load_cost_csv(settings.COST_PRICES_CSV)
