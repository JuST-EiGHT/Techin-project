"""Финансовые начисления из API Ozon. Все ответы помечены data_source="api" и предупреждением."""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.sync import _sync_now
from app.db.session import get_db
from app.models.user import User
from app.schemas.finance import (
    AccrualItem,
    AccrualProduct,
    AccrualsByDayResponse,
    AccrualsResponse,
    DayTotals,
    FinanceSyncRequest,
    Money,
    Period,
)
from app.schemas.sync import SyncRunResponse
from app.services.stores import get_current_store
from app.services.finance import Totals, daily_totals, list_accruals
from app.services.sync import finance as sync_finance
from app.services.sync.finance import MAX_PERIOD_DAYS, OZON_MIN_DATE, get_missing_periods

router = APIRouter(prefix="/finance", tags=["finance"])

DEFAULT_DAYS = 30
MAX_READ_DAYS = 366


def _period(date_from: date | None, date_to: date | None, max_days: int) -> tuple[date, date]:
    date_to = date_to or sync_finance.today_msk()
    date_from = date_from or date_to - timedelta(days=DEFAULT_DAYS - 1)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days + 1 > max_days:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {max_days} дней за раз")
    return date_from, date_to


def _money(t: Totals) -> Money:
    return Money(income=t.income, expense=t.expense, net=t.net)


@router.get("/accruals", response_model=AccrualsResponse)
async def accruals(
    date_from: date | None = None,
    date_to: date | None = None,
    type_id: int | None = None,
    product_id: uuid.UUID | None = None,
    posting_number: str | None = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    date_from, date_to = _period(date_from, date_to, MAX_READ_DAYS)
    rows, total, totals = await list_accruals(
        db, user.id, date_from, date_to, type_id=type_id, product_id=product_id,
        posting_number=posting_number, limit=limit, offset=offset,
    )
    items = [
        AccrualItem(
            id=a.id, accrual_date=a.accrual_date, type_id=t.id, type_name=t.name_ru, category=t.category,
            amount=a.amount, quantity=a.quantity, posting_number=a.posting_number, sku=a.sku,
            product=AccrualProduct(id=p.id, offer_id=p.offer_id, name=p.name) if p else None,
        )
        for a, t, p in rows
    ]
    return AccrualsResponse(
        date_from=date_from, date_to=date_to, total=total, limit=limit, offset=offset, totals=_money(totals), items=items
    )


@router.get("/accruals/by-day", response_model=AccrualsByDayResponse)
async def accruals_by_day(
    date_from: date | None = None,
    date_to: date | None = None,
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    date_from, date_to = _period(date_from, date_to, MAX_READ_DAYS)
    days, totals = await daily_totals(db, user.id, date_from, date_to)
    missing = await get_missing_periods(db, user.id, max(date_from, OZON_MIN_DATE), date_to)
    return AccrualsByDayResponse(
        date_from=date_from, date_to=date_to, totals=_money(totals),
        days=[DayTotals(date=d, income=t.income, expense=t.expense, net=t.net, lines=n) for d, t, n in days],
        missing_periods=[Period(date_from=s, date_to=e) for s, e in missing],
    )


@router.post("/sync", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def finance_sync(
    data: FinanceSyncRequest = Body(default_factory=FinanceSyncRequest),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Без дат — вчера, сегодня и незагруженные дни за 90 дней; с датами — указанный период заново."""
    params = None
    if data.date_from and data.date_to:
        date_from, date_to = _period(data.date_from, data.date_to, MAX_PERIOD_DAYS)
        if date_from < OZON_MIN_DATE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: Ozon отдаёт начисления начиная с 01.01.2022")
        if date_to > sync_finance.today_msk():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата окончания не может быть в будущем")
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
    return await _sync_now("finance", user, db, params)
