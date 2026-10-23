"""Основа аналитики (шаг 10.3): показатели текущего магазина за период, категории и товары для фильтров; лучшие и
худшие товары «Обзора бизнеса» (шаг 10.7)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.analytics import Category, Leaders, Metrics, ProductOption
from app.services import analytics as svc
from app.services.stores import get_current_store

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/metrics", response_model=Metrics)
async def get_metrics(
    date_from: date,
    date_to: date,
    granularity: str = Query("day", description="day | week (пн–вс) | month"),
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Товары; можно несколько"),
    category_id: int | None = Query(None, description="Категория Ozon; 0 — «Без категории»"),
    compare: bool = Query(False, description="Добавить прошлый сопоставимый период"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Выручка, прибыль, маржинальность, заказы, продажи и возвраты, средняя цена — по дням, неделям или месяцам, итоги
    периода и (compare=1) прошлый сопоставимый период. Дни без данных — пропуски (null), а не ноль."""
    try:
        return await svc.metrics(db, user.id, date_from, date_to, granularity, product_id, category_id, compare)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/leaders", response_model=Leaders)
async def get_leaders(
    date_from: date,
    date_to: date,
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Только среди этих товаров"),
    category_id: int | None = Query(None, description="Только среди товаров категории; 0 — «Без категории»"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """«Обзор бизнеса»: лучшие и худшие товары по прибыли за период и по изменению продаж к прошлому сопоставимому
    периоду (только значимые — не случайные колебания)."""
    try:
        return await svc.leaders(db, user.id, date_from, date_to, product_id, category_id)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/categories", response_model=list[Category])
async def get_categories(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Категории товаров магазина (не архивных) с числом товаров."""
    return await svc.categories(db, user.id)


@router.get("/products", response_model=list[ProductOption])
async def find_products(
    search: str | None = Query(None, max_length=100, description="Часть артикула, названия или SKU"),
    id: list[uuid.UUID] = Query(default_factory=list, description="Конкретные товары — подписать выбранные в фильтре"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Товары для фильтра «товар» — до 20 подходящих."""
    return await svc.search_products(db, user.id, search, ids=id or None)
