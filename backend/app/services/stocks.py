"""Текущие остатки для API: последний снепшот по каждой паре товар × склад + дни запаса и статус."""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.stocks import days_of_supply, stock_status
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse

SALES_WINDOW_DAYS = 30
NO_DATA = "no_data"  # значение фильтра status для строк без данных о продажах

Pair = tuple[uuid.UUID, uuid.UUID]  # product_id, warehouse_id


@dataclass
class StockRow:
    stock: ProductStock
    product: Product
    warehouse: Warehouse
    avg_daily_sales: Decimal | None
    days_of_supply: Decimal | None
    status: str | None


async def avg_daily_sales(db: AsyncSession, user_id: uuid.UUID, days: int = SALES_WINDOW_DAYS) -> dict[Pair, Decimal]:
    """Среднесуточные продажи пары товар × склад за `days` дней.

    Продаж в БД пока нет — они появятся с финансовыми данными (шаг 1.4). До тех пор
    пусто, и дни запаса со статусом остаются None («нет данных»), а не 0.
    """
    return {}


async def latest_stocks(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> list[tuple[ProductStock, Product, Warehouse]]:
    latest = (
        select(ProductStock.product_id, ProductStock.warehouse_id, func.max(ProductStock.snapshot_at).label("mx"))
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(Warehouse.user_id == user_id)
        .group_by(ProductStock.product_id, ProductStock.warehouse_id)
        .subquery()
    )
    q = (
        select(ProductStock, Product, Warehouse)
        .join(
            latest,
            and_(
                ProductStock.product_id == latest.c.product_id,
                ProductStock.warehouse_id == latest.c.warehouse_id,
                ProductStock.snapshot_at == latest.c.mx,
            ),
        )
        .join(Product, Product.id == ProductStock.product_id)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(Product.user_id == user_id)
        .order_by(Product.offer_id, Warehouse.name)
    )
    if warehouse_id:
        q = q.where(ProductStock.warehouse_id == warehouse_id)
    if product_id:
        q = q.where(ProductStock.product_id == product_id)
    return [tuple(r) for r in (await db.execute(q)).all()]


async def stock_rows(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: str | None = None,
    include_empty: bool = False,
) -> list[StockRow]:
    sales = await avg_daily_sales(db, user_id)
    rows: list[StockRow] = []
    for stock, product, warehouse in await latest_stocks(db, user_id, warehouse_id=warehouse_id, product_id=product_id):
        if not include_empty and not stock.present and not stock.reserved and not stock.in_transit:
            continue
        avg = sales.get((product.id, warehouse.id))
        row = StockRow(
            stock, product, warehouse, avg, days_of_supply(stock.present, avg), stock_status(stock.present, avg)
        )
        if status and (row.status or NO_DATA) != status:
            continue
        rows.append(row)
    return rows
