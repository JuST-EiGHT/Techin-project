"""Изменение цены товара в Ozon (шаг 6.2): предпросмотр → применение только по preview_id.

Расчёт минимальной и целевой цены (ничего не меняет) — /price (шаг 4.2). Здесь — изменение цены в Ozon: правила
безопасности BUSINESS_LOGIC §8, каждая попытка — в price_changes и audit_log.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.product import Product
from app.models.user import User
from app.schemas.prices import PriceApplyRequest, PriceApplyResult, PricePreview, PricePreviewRequest
from app.services import audit
from app.services import price_updater as svc
from app.services.auth import get_current_user

router = APIRouter(prefix="/prices", tags=["prices"])


@router.post("/preview", response_model=PricePreview, dependencies=[Depends(audit.read_only)])
async def preview(data: PricePreviewRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Было → станет, маржа, проверки безопасности. В Ozon ничего не меняется; предпросмотр живёт 15 минут."""
    product = await db.get(Product, data.product_id)
    if product is None or product.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    try:
        return await svc.preview(db, user.id, product, data.price, data.reason)
    except svc.PriceUpdateError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.post("/apply", response_model=PriceApplyResult)
async def apply(
    data: PriceApplyRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Применить предпросмотр: проверки заново, запрос в Ozon, журнал. Без предпросмотра — нельзя."""
    try:
        return await svc.apply(db, user.id, data.preview_id, request)
    except svc.PriceUpdateError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
