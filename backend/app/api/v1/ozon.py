"""Выплаты и баланс у Ozon (шаг 3.6) — область «Финансы Ozon»: работает без банков и планирования."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.ozon_payouts import BalanceDay, PayoutSettingsIn, PayoutSettingsRow, PayoutSettingsState, PayoutsOut
from app.services import ozon_payouts as svc
from app.services.auth import get_current_user
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/ozon", tags=["ozon"])


@router.get("/payouts", response_model=PayoutsOut)
async def get_payouts(
    horizon_weeks: int = Query(8, ge=1, le=12, description="На сколько недель вперёд прогноз"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """История выплат за 12 недель, график (свой, по выплатам или стандартный), прогноз и следующая выплата."""
    return await svc.payouts(db, user.id, horizon_weeks)


@router.get("/balance", response_model=list[BalanceDay])
async def get_balance(
    date_from: date | None = None,
    date_to: date | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Баланс у Ozon по дням: на начало, начислено, выплачено, на конец. Незагруженный день — null, а не 0."""
    date_to = date_to or today_msk()
    date_from = date_from or date_to - timedelta(days=29)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days + 1 > 366:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: не больше 366 дней за раз")
    return await svc.balance_days(db, user.id, date_from, date_to)


def _row(r) -> PayoutSettingsRow:
    return PayoutSettingsRow(effective_from=r.effective_from, mode=r.mode, weekday=r.weekday, lag_weeks=r.lag_weeks,
                             fee_pct=r.fee_pct)


async def _state(db: AsyncSession, user: User, created: bool | None = None) -> PayoutSettingsState:
    current, history = await svc.current_settings(db, user.id)
    std = svc.standard_schedule(today_msk())
    return PayoutSettingsState(
        current=_row(current) if current else None,
        history=[_row(r) for r in reversed(history)],
        standard={"mode": std.mode, "weekday": std.weekday, "lag_weeks": std.lag_weeks, "fee_pct": std.fee_pct,
                  "source": "standard", "title": std.title},
        created=created,
    )


@router.get("/payout-settings", response_model=PayoutSettingsState)
async def get_payout_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _state(db, user)


@router.put("/payout-settings", response_model=PayoutSettingsState)
async def save_payout_settings(
    body: PayoutSettingsIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Новый график — новая запись с сегодняшнего дня; прежние остаются в истории."""
    try:
        _, created = await svc.save_settings(db, user.id, svc.SettingsIn(**body.model_dump()))
    except svc.PayoutSettingsError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await db.commit()
    return await _state(db, user, created)
