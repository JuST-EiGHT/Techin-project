"""Товары (шаг 4.1): таблица с ценой, комиссией, себестоимостью, остатком, маржой и акциями; CSV; карточка."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.engine.products import FILTERS, SORTS
from app.models.user import User
from app.schemas.products import ProductDetail, ProductsPage
from app.services import products as svc
from app.services.auth import get_current_user
from app.services.stocks import SALES_WINDOW_DAYS
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/products", tags=["products"])

Sort = Literal[SORTS]  # type: ignore[valid-type]
Order = Literal["asc", "desc"]

_FILTER_HELP = (
    "Быстрые фильтры через запятую, товар подходит под все: negative — маржа ниже нуля, low_stock — меньше 14 дней "
    "запаса, critical_stock — меньше 7, in_promo — в акции, no_cost — нет себестоимости"
)


def _filters(text: str | None) -> set[str]:
    chosen = {f.strip() for f in (text or "").split(",") if f.strip()}
    unknown = chosen - set(FILTERS)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Неизвестный фильтр: {', '.join(sorted(unknown))}")
    return chosen


async def _selected(db: AsyncSession, user: User, search, filters, archived, sort, order):
    rows, ctx = await svc.product_rows(db, user.id)
    chosen, counts = svc.select_rows(
        rows, search=(search or "").strip() or None, filters=_filters(filters), archived=archived, sort=sort, order=order
    )
    return chosen, counts, ctx


@router.get("", response_model=ProductsPage)
async def list_products(
    search: str | None = Query(None, max_length=100, description="Часть артикула, названия, SKU или ID товара"),
    filters: str | None = Query(None, description=_FILTER_HELP),
    archived: bool = Query(False, description="true — товары в архиве Ozon, false — в продаже"),
    sort: Sort = Query("offer_id"),
    order: Order = Query("asc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows, counts, ctx = await _selected(db, user, search, filters, archived, sort, order)
    return ProductsPage(
        items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset, counts=counts,
        sales_known=bool(ctx.sales), stocks_known=ctx.stocks_known, sales_window_days=SALES_WINDOW_DAYS,
        margin_note=svc.MARGIN_NOTE,
    )


@router.get("/export", response_class=Response)
async def export_products(
    search: str | None = Query(None, max_length=100),
    filters: str | None = Query(None, description=_FILTER_HELP),
    archived: bool = Query(False),
    sort: Sort = Query("offer_id"),
    order: Order = Query("asc"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Те же товары, что на странице (поиск, фильтры, сортировка), — все строки, все колонки."""
    rows, _, _ = await _selected(db, user, search, filters, archived, sort, order)
    return attachment(svc.to_csv(rows), "text/csv; charset=utf-8", f"Товары {today_msk():%d.%m.%Y}.csv", "products.csv")


@router.get("/{product_id}", response_model=ProductDetail)
async def product_detail(
    product_id: uuid.UUID,
    days: int = Query(30, ge=7, le=365, description="За сколько дней история цены"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await svc.detail(db, user.id, product_id, days)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    return data
