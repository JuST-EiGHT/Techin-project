"""Главная — дашборд Ozon (шаг 3.7): только данные Ozon; обзор планирования — отдельно (3.10)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.dashboard import OzonDashboard
from app.services import dashboard as svc
from app.services.auth import get_current_user
from app.services.pnl_service import PnLRequest, PnLRequestError
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/ozon", response_model=OzonDashboard)
async def get_ozon_dashboard(
    date_from: date | None = Query(None, description="По умолчанию — начало месяца даты окончания"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня (МСК)"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Продано, к получению от Ozon, прибыль и маржа (с прошлым таким же периодом), на балансе у Ozon, следующая
    выплата; выручка по дням или неделям рядом с прошлым периодом, выплаты по неделям — факт и прогноз."""
    date_to = date_to or today_msk()
    date_from = date_from or date_to.replace(day=1)
    try:
        PnLRequest(date_from, date_to).check()
    except PnLRequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return await svc.ozon_dashboard(db, user.id, date_from, date_to)
