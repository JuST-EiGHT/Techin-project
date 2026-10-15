"""Склады и остатки (данные синхронизации из Ozon)."""
import uuid
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas.stock import StockResponse, WarehouseResponse
from app.schemas.sync import utc
from app.services.auth import get_current_user
from app.services.stocks import latest_stocks, stock_rows

warehouses_router = APIRouter(prefix="/warehouses", tags=["stocks"])
stocks_router = APIRouter(prefix="/stocks", tags=["stocks"])

StatusFilter = Literal["critical", "low", "normal", "excess", "no_data"]


@warehouses_router.get("", response_model=list[WarehouseResponse])
async def list_warehouses(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Warehouse).where(Warehouse.user_id == user.id).order_by(Warehouse.is_active.desc(), Warehouse.type, Warehouse.name)
    )
    totals: dict[uuid.UUID, list[int]] = defaultdict(lambda: [0, 0, 0])
    for stock, _, warehouse in await latest_stocks(db, user.id):
        t = totals[warehouse.id]
        t[0] += stock.present > 0
        t[1] += stock.present
        t[2] += stock.in_transit or 0
    return [
        WarehouseResponse(
            id=w.id, ozon_warehouse_id=w.ozon_warehouse_id, name=w.name, type=w.type, cluster_name=w.cluster_name,
            is_active=w.is_active, synced_at=utc(w.synced_at),
            products_in_stock=totals[w.id][0], total_present=totals[w.id][1], total_in_transit=totals[w.id][2],
        )
        for w in res.scalars()
    ]


@stocks_router.get("", response_model=list[StockResponse])
async def list_stocks(
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: StatusFilter | None = None,
    include_empty: bool = Query(False, description="Показывать нулевые остатки (товар ушёл со склада)"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await stock_rows(
        db, user.id, warehouse_id=warehouse_id, product_id=product_id, status=status, include_empty=include_empty
    )
    return [StockResponse.from_row(r) for r in rows]
