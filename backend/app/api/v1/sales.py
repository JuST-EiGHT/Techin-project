"""Продажи по товарам по дням (шаг 3.5) — для графиков, дней запаса, поставок и аномалий."""
import uuid
from dataclasses import asdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.product import Product
from app.models.user import User
from app.schemas.sales import SalesDaily, SalesRebuild
from app.services import sales as svc
from app.services.auth import get_current_user
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/sales", tags=["sales"])
MAX_DAYS = 366


@router.get("/daily", response_model=SalesDaily)
async def sales_daily(
    product_id: uuid.UUID | None = Query(None, description="Товар каталога Ozon"),
    offer_id: str | None = Query(None, max_length=100, description="Артикул — если товара нет в каталоге"),
    date_from: date | None = Query(None, description="По умолчанию — 30 дней до даты окончания"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня (МСК)"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Продано, возвращено и выручка по дням — товара или всех товаров. Дни без данных — null, а не 0."""
    date_to = date_to or today_msk()
    date_from = date_from or date_to - timedelta(days=29)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {MAX_DAYS} дней за раз")
    if product_id is not None:
        product = await db.get(Product, product_id)
        if product is None or product.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
        offer_id = product.offer_id
    return await svc.daily(db, user.id, date_from, date_to, offer_id.strip() if offer_id else None)


@router.post("/rebuild", response_model=SalesRebuild)
async def rebuild_sales(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Пересобрать продажи за всю историю начислений. Обычно не нужно: продажи пересобираются сами после
    синхронизации финансов и действий с отчётами."""
    return asdict(await svc.rebuild_all(db, user.id))
