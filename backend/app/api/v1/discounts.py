"""Заявки покупателей на скидку (шаг 4.3a): список с расчётом прибыли, Excel, заявка подробно, решение через Ozon
(предпросмотр → подтверждение → журнал), правило подсказки «одобрили бы при марже ≥ X %». Данные — текущего магазина."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.models.user import User
from app.schemas.discount import (
    DecisionApplyIn,
    DecisionPreview,
    DecisionPreviewIn,
    DecisionResult,
    DiscountDetail,
    DiscountList,
    RuleIn,
    RuleOut,
)
from app.services import audit
from app.services import discounts as svc
from app.services.discount_export import to_xlsx
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk
from app.services.user_settings import (
    anomaly_thresholds,
    discount_rule_margin,
    get_settings,
    low_margin,
)

router = APIRouter(prefix="/discounts", tags=["discounts"])

Tab = Literal["new", "approved", "declined", "expired", "all"]
Sort = Literal[tuple(svc.SORTS)]  # type: ignore[valid-type]


def _fail(exc: svc.DiscountError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


async def _page(db: AsyncSession, user: User, tab: str, search: str | None, date_from: date | None,
                date_to: date | None, marks: list[str] | None, product_id: uuid.UUID | None, sort: str | None,
                order: str) -> dict:
    marks = [m for m in marks or [] if m in svc.MARK_FILTERS]
    rows = await svc.all_rows(db, user.id, product_id)
    return svc.list_page(rows, tab=tab, search=search, date_from=date_from, date_to=date_to, marks=marks, sort=sort,
                         order=order)


@router.get("", response_model=DiscountList)
async def list_discounts(
    tab: Tab = "new",
    search: str | None = Query(None, max_length=200, description="артикул, название, SKU, штрихкод"),
    date_from: date | None = None,
    date_to: date | None = None,
    marks: list[str] | None = Query(None, description="loss | below_ozon_min | low_margin | rule_ok | no_calc"),
    product_id: uuid.UUID | None = None,
    sort: Sort | None = None,
    order: Literal["asc", "desc"] = "desc",
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Заявки вкладки (по умолчанию — ждут решения, срочные первыми) и счётчики вкладок."""
    page = await _page(db, user, tab, search, date_from, date_to, marks, product_id, sort, order)
    settings = await get_settings(db, user.id)
    return DiscountList(**page, low_margin_pct=low_margin(settings), rule_margin_pct=discount_rule_margin(settings),
                        expiry_hours=anomaly_thresholds(settings).discount_expiry_hours)


@router.get("/export", response_class=Response)
async def export_discounts(
    tab: Tab = "new",
    search: str | None = Query(None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    marks: list[str] | None = Query(None),
    product_id: uuid.UUID | None = None,
    sort: Sort | None = None,
    order: Literal["asc", "desc"] = "desc",
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Те же строки в Excel."""
    page = await _page(db, user, tab, search, date_from, date_to, marks, product_id, sort, order)
    return attachment(to_xlsx(page["items"]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      f"Заявки на скидку {today_msk():%d.%m.%Y}.xlsx", "discounts.xlsx")


@router.put("/rule", response_model=RuleOut)
async def set_rule(data: RuleIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Порог подсказки «одобрили бы по правилу „маржа ≥ X %“»; null — подсказка выключена. Сервис сам ничего не
    одобряет — автоматизация будет в шагах 9.x."""
    settings = await get_settings(db, user.id)
    settings.discount_rule_margin_pct = data.margin_pct
    db.add(settings)
    await db.commit()
    return RuleOut(margin_pct=discount_rule_margin(settings))


@router.post("/preview", response_model=DecisionPreview, dependencies=[Depends(audit.read_only)])
async def preview_decision(data: DecisionPreviewIn, user: User = Depends(get_current_store),
                           db: AsyncSession = Depends(get_db)):
    """Что уйдёт в Ozon и можно ли — по каждой заявке. В Ozon ничего не меняется; предпросмотр живёт 15 минут."""
    items = [svc.Item(i.task_id, i.action, i.price, i.quantity) for i in data.items]
    try:
        return await svc.preview(db, user.id, items, data.comment)
    except svc.DiscountError as exc:
        raise _fail(exc) from None


@router.post("/apply", response_model=DecisionResult)
async def apply_decision(data: DecisionApplyIn, request: Request, user: User = Depends(get_current_store),
                         db: AsyncSession = Depends(get_db)):
    """Отправить решения предпросмотра в Ozon — один раз. Итог и ошибка Ozon — по каждой заявке."""
    try:
        result = await svc.apply(db, user.id, data.batch_id, confirmed=data.confirmed, accept_loss=data.accept_loss,
                                 request=request)
    except svc.DiscountError as exc:
        raise _fail(exc) from None
    await _refresh_alerts(db, user.id)
    return result


async def _refresh_alerts(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Решённые заявки уходят из колокольчика и «Требует внимания» сразу, не дожидаясь проверки по расписанию."""
    import logging

    from app.services.anomalies import detect

    try:
        await detect(db, user_id, only=("discounts",))
    except Exception:  # noqa: BLE001 — решение уже принято, уведомления обновятся по расписанию
        await db.rollback()
        logging.getLogger(__name__).exception("Заявки на скидку: уведомления после решения не обновлены")


@router.get("/{task_id}", response_model=DiscountDetail)
async def discount_detail(task_id: uuid.UUID, user: User = Depends(get_current_store),
                          db: AsyncSession = Depends(get_db)):
    """Заявка подробно: история статусов и решения через сервис."""
    try:
        return await svc.detail(db, user.id, task_id)
    except svc.DiscountError as exc:
        raise _fail(exc) from None
