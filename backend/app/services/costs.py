"""Себестоимость товаров для расчётов.

Справочника себестоимости в БД пока нет — он появится на шаге 2.3. До тех пор пусто,
и расчёты, которым нужна себестоимость (маржа в акции), возвращают None, а не 0
(правило 5: неизвестная себестоимость ≠ 0).
"""
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession


async def cost_prices(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """product_id → себестоимость единицы без НДС."""
    return {}
