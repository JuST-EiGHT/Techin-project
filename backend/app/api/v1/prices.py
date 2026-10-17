"""Изменение цен в Ozon: одного товара (6.2), списком (6.3), история и откат (6.4) — предпросмотр → применение только по
его id.

Расчёт минимальной и целевой цены (ничего не меняет) — /price (шаг 4.2). Здесь — изменение цены в Ozon: правила
безопасности BUSINESS_LOGIC §8, каждая попытка — в price_changes и audit_log.
"""
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.models.product import Product
from app.models.user import User
from app.schemas.prices import (
    BulkApplyRequest,
    BulkPreview,
    BulkPreviewRequest,
    BulkStatus,
    PriceApplyRequest,
    PriceApplyResult,
    PriceHistoryPage,
    PricePreview,
    PricePreviewRequest,
)
from app.services import audit, pricing
from app.services import price_bulk as bulk
from app.services import price_history as history_svc
from app.services import price_updater as svc
from app.services.auth import get_current_user
from app.services.promotion_history import MAX_DAYS
from app.services.sync.finance import today_msk
from app.services.templates import MEDIA_TYPES

MAX_FILE_MB = 5
HISTORY_DAYS = 30

router = APIRouter(prefix="/prices", tags=["prices"])


@router.post("/preview", response_model=PricePreview, dependencies=[Depends(audit.read_only)])
async def preview(
    data: PricePreviewRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
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
    data: PriceApplyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Применить предпросмотр: проверки заново, запрос в Ozon, журнал. Без предпросмотра — нельзя."""
    try:
        return await svc.apply(db, user.id, data.preview_id, request)
    except svc.PriceUpdateError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.post("/rollback/{change_id}", response_model=PricePreview, dependencies=[Depends(audit.read_only)])
async def rollback_preview(
    change_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Предпросмотр отката (шаг 6.4): вернуть цену, которая была до изменения. Применить — POST /prices/apply с
    preview_id. Откат — только изменения не старше 7 дней, откат отката запрещён (BUSINESS_LOGIC §8)."""
    try:
        return await svc.rollback_preview(db, user.id, change_id)
    except svc.PriceUpdateError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.get("/history", response_model=PriceHistoryPage)
async def price_history(
    product_id: uuid.UUID | None = Query(None, description="Товар; без него — все"),
    status_: Literal[history_svc.STATUSES + (history_svc.NOT_APPLIED,)] | None = Query(  # type: ignore[valid-type]
        None, alias="status", description="applied | blocked | rejected | failed | unknown | queued | not_applied"
    ),
    kind: Literal[history_svc.KINDS] | None = Query(  # type: ignore[valid-type]
        None, description="single | bulk | rollback | auto"
    ),
    search: str | None = Query(None, max_length=100, description="Артикул или название"),
    date_from: date | None = Query(None, description="По умолчанию — 30 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """История изменений цен через сервис — применённые и нет, новые первыми; можно ли откатить."""
    end = date_to or today_msk()
    start = date_from or end - timedelta(days=HISTORY_DAYS - 1)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {MAX_DAYS} дней за раз")
    if product_id is not None:
        product = await db.get(Product, product_id)
        if product is None or product.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    return await history_svc.history(
        db, user.id, start, end, product_id=product_id, status=status_, kind=kind,
        search=(search or "").strip() or None, limit=limit, offset=offset,
    )


# ── массовое изменение (шаг 6.3) ──
def _fail(exc: svc.PriceUpdateError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


@router.post("/bulk/preview", response_model=BulkPreview, dependencies=[Depends(audit.read_only)])
async def bulk_preview(
    data: BulkPreviewRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Массовый предпросмотр: по стратегии для выбранных товаров или по списку цен. Группы «применится», «с
    предупреждением», «заблокировано»; в Ozon ничего не меняется, предпросмотр живёт 15 минут."""
    try:
        if data.strategy is not None:
            s = data.strategy
            return await bulk.preview(db, user.id, strategy=bulk.Strategy(s.kind, s.value, s.limit_step),
                                      product_ids=data.product_ids, reason=data.reason)
        return await bulk.preview(db, user.id, prices=[(i.product_id, i.price) for i in data.items], reason=data.reason)
    except svc.PriceUpdateError as exc:
        raise _fail(exc) from exc


@router.get("/bulk/template", response_class=Response)
async def bulk_template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format", description="xlsx или csv"),
    filters: str | None = Query(None, description="Как в таблице цен: below_min, below_target, no_data, promo_loss"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Шаблон с товарами в продаже и текущими ценами: поправить колонку «Новая цена» и загрузить."""
    chosen = {f.strip() for f in (filters or "").split(",") if f.strip()}
    if unknown := chosen - set(pricing.FILTERS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Неизвестный фильтр: {', '.join(sorted(unknown))}")
    content = await bulk.template(db, user.id, fmt, chosen)
    return attachment(content, MEDIA_TYPES[fmt], f"Цены {today_msk():%d.%m.%Y}.{fmt}", f"prices.{fmt}")


async def _read_upload(file: UploadFile) -> bytes:
    limit = MAX_FILE_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Файл больше {MAX_FILE_MB} МБ")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    return content


@router.post("/bulk/upload", response_model=BulkPreview, dependencies=[Depends(audit.read_only)])
async def bulk_upload(
    file: UploadFile = File(..., description="Шаблон цен (XLSX или CSV) с колонкой «Новая цена»"),
    reason: str | None = Form(None, max_length=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Файл → тот же массовый предпросмотр и проверки. Файл без правок — 0 изменений; ошибки строк — в file.errors."""
    content = await _read_upload(file)
    try:
        return await bulk.upload(db, user.id, content, file.filename or "", reason)
    except svc.PriceUpdateError as exc:
        raise _fail(exc) from exc


@router.post("/bulk/upload/errors", response_class=Response, dependencies=[Depends(audit.read_only)])
async def bulk_upload_errors(
    file: UploadFile = File(..., description="Тот же файл, что загружали в /bulk/upload"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Строки с ошибками и не прошедшие проверки — тем же шаблоном с колонкой «Ошибка»."""
    content = await _read_upload(file)
    try:
        out, fmt = await bulk.error_file(db, user.id, content, file.filename or "")
    except svc.PriceUpdateError as exc:
        raise _fail(exc) from exc
    return attachment(out, MEDIA_TYPES[fmt], f"Цены — строки с ошибками.{fmt}", f"prices-errors.{fmt}")


@router.post("/bulk/apply", response_model=BulkStatus)
async def bulk_apply(
    data: BulkApplyRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Применить массовый предпросмотр: больше 10 товаров — confirmed, больше 50 — confirm_word «ПОДТВЕРЖДАЮ». В Ozon —
    по 100 товаров с паузой 2 с; ход виден в GET /prices/bulk/{batch_id}."""
    try:
        return await bulk.apply(db, user.id, data.batch_id, include_warnings=data.include_warnings,
                                confirmed=data.confirmed, confirm_word=data.confirm_word, request=request)
    except svc.PriceUpdateError as exc:
        raise _fail(exc) from exc


@router.get("/bulk/{batch_id}", response_model=BulkStatus)
async def bulk_status(batch_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Ход и итог массового изменения: по каждому товару и по каждому запросу в Ozon."""
    try:
        return await bulk.status(db, user.id, batch_id)
    except svc.PriceUpdateError as exc:
        raise _fail(exc) from exc
