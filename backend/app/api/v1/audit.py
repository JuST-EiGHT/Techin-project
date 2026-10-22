"""Журнал действий (шаг 6.1): изменения цен и цен в акциях — что меняли, до и после, запрос и ответ Ozon, откуда.

Только чтение: записи журнала не меняются и не удаляются.
"""
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.product import Product
from app.models.user import User
from app.schemas.audit import AuditPage
from app.services import audit as svc
from app.services.stores import get_current_store
from app.services.promotion_history import MAX_DAYS
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/audit", tags=["audit"])

DEFAULT_DAYS = 90


@router.get("/prices", response_model=AuditPage)
async def price_log(
    product_id: uuid.UUID | None = Query(None, description="Товар; без него — все товары"),
    kind: Literal["price", "promotion"] | None = Query(
        None, description="price — цена товара, promotion — вход, выход и цена в акциях; без него — всё"
    ),
    status_: Literal[svc.STATUSES] | None = Query(  # type: ignore[valid-type]
        None, alias="status", description="applied | rejected | failed | unknown"
    ),
    action: str | None = Query(None, max_length=64, description="Например promotion.join"),
    date_from: date | None = Query(None, description="По умолчанию — 90 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Действия с ценами, новые первыми: что меняли, данные до и после, запрос и ответ Ozon, итог, IP и браузер."""
    end = date_to or today_msk()
    start = date_from or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {MAX_DAYS} дней за раз")
    if product_id is not None:
        product = await db.get(Product, product_id)
        if product is None or product.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    items, total = await svc.price_log(
        db, user.id, start, end, product_id=product_id, kind=kind, status=status_, action=action, limit=limit,
        offset=offset,
    )
    return {"date_from": start, "date_to": end, "total": total, "limit": limit, "offset": offset, "items": items}
