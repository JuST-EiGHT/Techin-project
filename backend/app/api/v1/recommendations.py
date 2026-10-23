"""«Рекомендуемые действия» (шаг 10.6): список по магазину или товару, «Скрыть» на N дней, «Вернуть скрытые»."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.recommendation import HideIn, HideOut, RecommendationsPage, UnhideOut
from app.services import recommendations as svc
from app.services.stores import get_current_store
from app.services.user_settings import get_settings, recommendation_hide_days

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationsPage)
async def list_recommendations(
    product_id: uuid.UUID | None = Query(None, description="Только по этому товару («Статистика товара»)"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Что сделать: акция в минус, цена ниже минимальной, поставка «Срочно / Скоро», себестоимость, вывоз. Данных мало
    (меньше 14 дней продаж с данными за 30) — пусто и enough_data=false. Скрытые не показываются."""
    return await svc.build(db, user.id, product_id)


@router.post("/hide", response_model=HideOut)
async def hide(data: HideIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Скрыть» на N дней (настройка магазина); условие останется — рекомендация вернётся после срока."""
    until = await svc.hide(db, user.id, data.key)
    return {"hidden_until": until, "hide_days": recommendation_hide_days(await get_settings(db, user.id))}


@router.post("/unhide", response_model=UnhideOut)
async def unhide(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Вернуть скрытые» — все, что скрыты сейчас."""
    return {"restored": await svc.unhide_all(db, user.id)}
