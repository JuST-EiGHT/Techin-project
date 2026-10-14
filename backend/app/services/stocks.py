"""Текущие остатки для API: последний снепшот по каждой паре товар × склад + дни запаса и статус."""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.stocks import days_of_supply, stock_status
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse
from app.services.sales import avg_daily_by_product

SALES_WINDOW_DAYS = 30
NO_DATA = "no_data"  # значение фильтра status для строк без данных о продажах



@dataclass
class StockRow:
    stock: ProductStock
    product: Product
    warehouse: Warehouse
    avg_daily_sales: Decimal | None  # по товару в целом
    days_of_supply: Decimal | None  # остаток товара на всех складах / продажи в день
    status: str | None
    product_available: int  # доступно к продаже на всех складах


async def avg_daily_sales(db: AsyncSession, user_id: uuid.UUID, days: int = SALES_WINDOW_DAYS) -> dict[uuid.UUID, Decimal]:
    """Продажи в день по товару за `days` дней (шаг 3.5, `services/sales`). Склада в начислениях Ozon нет —
    продажи и дни запаса по товару в целом: весь остаток на всех складах / продажи в день.
    Мало дней с данными — пусто, и дни запаса со статусом None («нет данных»), а не 0."""
    return await avg_daily_by_product(db, user_id, days)


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
    every = await latest_stocks(db, user_id, product_id=product_id)  # все склады — остаток товара в целом
    total: dict[uuid.UUID, int] = {}
    for stock, product, _ in every:
        total[product.id] = total.get(product.id, 0) + max(stock.present or 0, 0)
    rows: list[StockRow] = []
    for stock, product, warehouse in every:
        if warehouse_id and warehouse.id != warehouse_id:
            continue
        if not include_empty and not stock.present and not stock.reserved and not stock.in_transit:
            continue
        avg = sales.get(product.id)
        available = total[product.id]
        row = StockRow(
            stock, product, warehouse, avg, days_of_supply(available, avg), stock_status(available, avg), available
        )
        if status and (row.status or NO_DATA) != status:
            continue
        rows.append(row)
    return rows
