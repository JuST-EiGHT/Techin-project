"""P&L за любой период (шаг 3.2): по начислениям из отчётов XLSX и Ozon API, источник — в ответе."""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.core.files import attachment
from app.models.user_settings import COST_LABELS
from app.schemas.pnl import PeriodPnL, PnLByDay, PnLPeriods, ProductsPage, Source
from app.services import pnl_service as svc
from app.services import analytics, pnl_statement, product_pnl
from app.services import reports as reports_svc
from app.services.stores import get_current_store
from app.services.user_settings import get_settings, load_low_margin
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
    req: svc.PnLRequest = Depends(_request), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """P&L за период: сколько пришло от Ozon, себестоимость с НДС, налог, остаток, маржа.
    Какой источник за сколько дней использован и каких дней нет — в coverage и warnings. statement — тот же P&L строками
    отчёта о прибылях и убытках с прочими расходами бизнеса из «Операций» (если включено «Планирование»)."""
    return await pnl_statement.extend(db, user.id, req, await svc.get_pnl(db, user.id, req))


async def _report(db: AsyncSession, user: User, report_id: uuid.UUID | None):
    if report_id is None:
        return None
    report = await reports_svc.get_report(db, user.id, report_id)
    if report is None or report.period_start is None or report.period_end is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Отчёт не найден")
    return report


SortField = Literal["revenue", "profit", "margin", "qty", "total"]


async def _products(db: AsyncSession, user: User, req: svc.PnLRequest, report_id: uuid.UUID | None) -> dict:
    try:
        return await product_pnl.compute(db, user.id, req, await _report(db, user, report_id))
    except svc.PnLRequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/by-product", response_model=ProductsPage)
async def get_pnl_by_product(
    req: svc.PnLRequest = Depends(_request),
    report_id: uuid.UUID | None = Query(None, description="Товары одного отчёта по начислениям — вместо периода"),
    search: str | None = Query(None, max_length=100, description="Артикул или название"),
    sort: SortField = Query("revenue"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    filter_: list[Literal["loss", "low_margin", "no_cost"]] = Query(
        default_factory=list, alias="filter", description="loss — прибыль < 0, low_margin — маржа ниже порога, no_cost — без себестоимости"),
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Только эти товары («Аналитика прибыльности»)"),
    category_id: int | None = Query(None, description="Только товары категории; 0 — «Без категории»"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Прибыль по товарам: продано, выручка, комиссия, логистика, прочее, себестоимость, прибыль до налога, маржа.
    Начисления без товара — в «Не распределено»; итоги — по всем товарам периода. Быстрые фильтры и выбор товаров или
    категории — для «Аналитики прибыльности» (10.9); filter_counts — числа у кнопок, low_margin — порог."""
    value = await _products(db, user, req, report_id)
    offers = await _filter_offers(db, user, product_id, category_id)
    low = await load_low_margin(db, user.id)
    picked = product_pnl.filter_rows(value["items"], filter_, low, offers)
    rows = product_pnl.select_rows(picked, search, sort, order)
    return {**value, "items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset,
            "filter_counts": product_pnl.filter_counts(value["items"], low, offers), "low_margin": low}


async def _filter_offers(db: AsyncSession, user: User, product_ids: list[uuid.UUID], category_id: int | None):
    """Артикулы выбранных товаров или категории; ничего не выбрано — None (все). Чужой товар — 404."""
    try:
        products = await analytics._products(db, user.id, product_ids, category_id)
    except analytics.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None
    return None if products is None else {p.offer_id for p in products}


@router.get("/by-product/export", response_class=Response)
async def export_pnl_by_product(
    req: svc.PnLRequest = Depends(_request),
    report_id: uuid.UUID | None = Query(None),
    search: str | None = Query(None, max_length=100),
    sort: SortField = Query("revenue"),
    order: Literal["asc", "desc"] = Query("desc"),
    filter_: list[Literal["loss", "low_margin", "no_cost"]] = Query(default_factory=list, alias="filter"),
    product_id: list[uuid.UUID] = Query(default_factory=list),
    category_id: int | None = Query(None),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Таблица «По товарам» в CSV для Excel — все строки по тому же поиску, фильтрам и сортировке."""
    value = await _products(db, user, req, report_id)
    offers = await _filter_offers(db, user, product_id, category_id)
    picked = product_pnl.filter_rows(value["items"], filter_, await load_low_margin(db, user.id), offers)
    rows = product_pnl.select_rows(picked, search, sort, order)
    if value["cost_basis"] == product_pnl.WITHOUT_VAT:
        cost_title = "Себестоимость без НДС, руб."
    else:
        cost_title = f"{COST_LABELS[(await get_settings(db, user.id)).supplier_payment]}, руб."
    period = f"{value['date_from'][8:10]}.{value['date_from'][5:7]}.{value['date_from'][:4]}–" \
             f"{value['date_to'][8:10]}.{value['date_to'][5:7]}.{value['date_to'][:4]}"
    content = product_pnl.to_csv(value, rows, cost_title)
    return attachment(content, "text/csv; charset=utf-8", f"Прибыль по товарам {period}.csv", "products-pnl.csv")


@router.get("/by-day", response_model=PnLByDay)
async def get_pnl_by_day(
    req: svc.PnLRequest = Depends(_request),
    report_id: uuid.UUID | None = Query(None, description="Дни одного отчёта по начислениям — вместо периода"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """По дням: выручка, начисления, себестоимость с НДС, прибыль до налога. Нет данных за день — null."""
    report = await _report(db, user, report_id)
    try:
        return await svc.get_by_day(db, user.id, req, report)
    except svc.PnLRequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/periods", response_model=PnLPeriods)
async def get_pnl_periods(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """За какие периоды есть данные: отчёты XLSX, дни из Ozon API, покрытие по месяцам и сохранённые итоги."""
    return await svc.get_periods(db, user.id)
