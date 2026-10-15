"""Обзор «Планирования» и ДДС (шаг 3.10): деньги сейчас, прогноз и разрывы, ожидается за горизонт; ДДС по статьям."""
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.plan_calendar import CashFlowOut, OverviewOut
from app.services import planning as planning_svc
from app.services import planning_overview as svc
from app.services.auth import get_current_user
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/planning", tags=["planning"])
MAX_CASH_FLOW_DAYS = 731


@router.get("/overview", response_model=OverviewOut)
async def overview(
    horizon_days: int | None = Query(None, ge=7, le=180, description="4 / 8 недель (28 / 56) или 30 / 60 / 90 дней"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Деньги сейчас, ближайшая выплата Ozon, кассовый разрыв или «денег хватит до …», ожидается за горизонт; ряды по
    дням: две недели факта до сегодня и план с сегодня. Нет данных — null, а не 0."""
    if horizon_days is None:
        horizon_days = (await planning_svc.state(db, user.id))["horizon_days"]
    await planning_svc.sync_ozon_operations(db, user.id)
    return await svc.overview(db, user.id, horizon_days)


@router.get("/cash-flow", response_model=CashFlowOut)
async def cash_flow(
    date_from: date | None = Query(None, description="По умолчанию — начало месяца два месяца назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    granularity: Literal["week", "month"] = "month",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ДДС — по деньгам: остаток на начало, поступления и платежи по статьям и периодам, остаток на конец. Переводы
    между своими счетами — не приход и не расход. P&L по начислениям Ozon — в «Финансах», их не смешиваем."""
    date_to = date_to or today_msk()
    if date_from is None:
        first = date_to.replace(day=1)
        date_from = (first - timedelta(days=1)).replace(day=1)
        date_from = (date_from - timedelta(days=1)).replace(day=1)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days + 1 > MAX_CASH_FLOW_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: не больше двух лет за раз")
    await planning_svc.sync_ozon_operations(db, user.id)
    return await svc.cash_flow(db, user.id, date_from, date_to, granularity)
