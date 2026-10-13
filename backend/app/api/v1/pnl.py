"""P&L за любой период (шаг 3.2): по начислениям из отчётов XLSX и Ozon API, источник — в ответе."""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.pnl import PeriodPnL, PnLByDay, PnLPeriods, Source
from app.services import pnl_service as svc
from app.services import reports as reports_svc
from app.services.auth import get_current_user
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/pnl", tags=["pnl"])

SOURCE_DESCRIPTION = (
    "auto — за каждый день отчёт XLSX, если он есть (совпадает с кабинетом Ozon), иначе Ozon API; "
    "xlsx — только отчёты; api — только Ozon API"
)


def _request(
    date_from: date | None = Query(None, description="По умолчанию — начало месяца даты окончания"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня (МСК)"),
    source: Source = Query("auto", description=SOURCE_DESCRIPTION),
    vat_multiplier: Decimal | None = Query(None, ge=1, le=2, description="НДС поставщика коэффициентом, 1,22 = 22 %"),
    tax_rate: Decimal | None = Query(None, ge=0, le=1, description="Свой процент налога долей, 0,06 = 6 %"),
    tax_base: Literal["profit", "revenue"] | None = Query(None, description="К tax_rate: от прибыли или от дохода"),
    manual_total: Decimal | None = Query(None, description="Сколько реально пришло от Ozon за период"),
) -> svc.PnLRequest:
    """Параметры расчёта; не переданные — по налоговому профилю и справочнику себестоимости."""
    date_to = date_to or today_msk()
    req = svc.PnLRequest(
        date_from=date_from or date_to.replace(day=1), date_to=date_to, source=source,
        vat_multiplier=vat_multiplier, tax_rate=tax_rate, tax_base=tax_base, manual_total=manual_total,
    )
    try:
        req.check()
    except svc.PnLRequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return req


@router.get("", response_model=PeriodPnL)
async def get_pnl(
    req: svc.PnLRequest = Depends(_request), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """P&L за период: сколько пришло от Ozon, себестоимость с НДС, налог, остаток, маржа.
    Какой источник за сколько дней использован и каких дней нет — в coverage и warnings."""
    return await svc.get_pnl(db, user.id, req)


@router.get("/by-day", response_model=PnLByDay)
async def get_pnl_by_day(
    req: svc.PnLRequest = Depends(_request),
    report_id: uuid.UUID | None = Query(None, description="Дни одного отчёта по начислениям — вместо периода"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """По дням: выручка, начисления, себестоимость с НДС, прибыль до налога. Нет данных за день — null."""
    report = None
    if report_id is not None:
        report = await reports_svc.get_report(db, user.id, report_id)
        if report is None or report.period_start is None or report.period_end is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Отчёт не найден")
    try:
        return await svc.get_by_day(db, user.id, req, report)
    except svc.PnLRequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/periods", response_model=PnLPeriods)
async def get_pnl_periods(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """За какие периоды есть данные: отчёты XLSX, дни из Ozon API, покрытие по месяцам и сохранённые итоги."""
    return await svc.get_periods(db, user.id)
