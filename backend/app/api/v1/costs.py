"""Справочник себестоимости: список артикулов, правка с историей, импорт CSV/XLSX (шаг 2.3),
шаблоны «скачал → заполнил → загрузил» и файл строк с ошибками (шаг 2.4)."""

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.models.product_cost import ProductCost
from app.models.user import User
from app.schemas.costs import (
    CostRecord,
    CostRow,
    CostSaved,
    CostsPage,
    CostUpdate,
    ImportCounts,
    ImportResponse,
    ImportRowOut,
)
from app.services import audit
from app.services import costs as svc
from app.services.auth import get_current_user
from app.services.cost_import import TEMPLATE_NAMES, CostImportError, cost_template, error_file, import_costs
from app.services.pnl_stale import affected_period, invalidate
from app.services.report_costs import recost_reports
from app.services.sync.finance import today_msk
from app.services.templates import MEDIA_TYPES

router = APIRouter(prefix="/costs", tags=["costs"])

MAX_FILE_MB = 5


def _q(value: Decimal | None, places: str) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(Decimal(places))


def _row(info: svc.ArticleInfo, history: svc.CostHistory) -> CostRow:
    current: ProductCost | None = history.record_at(info.offer_id, today_msk())
    return CostRow(
        offer_id=info.offer_id,
        name=info.name,
        sku=info.sku,
        cost_price=_q(current.cost_price, "0.01") if current else None,
        vat_multiplier=_q(current.vat_multiplier, "0.001") if current else None,
        effective_from=current.effective_from if current else None,
        note=current.note if current else None,
        updated_at=current.created_at if current else None,
        history_count=len(history.history(info.offer_id)),
        in_catalog=info.in_catalog,
        report_qty=info.report_qty,
    )


@router.get("", response_model=CostsPage)
async def list_costs(
    search: str | None = Query(None, max_length=100, description="Часть артикула или названия"),
    missing: bool = Query(False, description="Только артикулы без себестоимости"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    history = await svc.load_cost_history(db, user.id)
    articles = await svc.known_articles(db, user.id, history)
    rows = [_row(info, history) for info in articles.values()]
    all_count, missing_count = len(rows), sum(r.cost_price is None for r in rows)
    if missing:
        rows = [r for r in rows if r.cost_price is None]
    if search and (q := search.strip().lower()):
        rows = [r for r in rows if q in r.offer_id.lower() or q in (r.name or "").lower()]
    # без себестоимости, но продавались — первыми: их надо заполнить в первую очередь
    rows.sort(key=lambda r: (r.cost_price is not None, -r.report_qty, r.offer_id))
    return CostsPage(items=rows, total=len(rows), all_count=all_count, missing_count=missing_count)


def _file(content: bytes, fmt: str, name: str, fallback: str) -> Response:
    return attachment(content, MEDIA_TYPES[fmt], f"{name}.{fmt}", f"{fallback}.{fmt}")


@router.get("/template", response_class=Response)
async def download_template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format", description="xlsx или csv"),
    rows: Literal["all", "missing", "empty"] = Query(
        "all", description="all — все товары с текущими ценами, missing — без себестоимости, empty — пустой"
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Шаблон себестоимости с товарами пользователя: скачал → заполнил → загрузил в /import."""
    content = await cost_template(db, user.id, rows, fmt)
    return _file(content, fmt, f"Себестоимость — {TEMPLATE_NAMES[rows]} {today_msk():%d.%m.%Y}", "costs")


@router.put("/{offer_id:path}", response_model=CostSaved)
async def set_cost(
    offer_id: str, data: CostUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Новая цена — новая запись в истории; прошлые записи не меняются."""
    offer_id = offer_id.strip()
    if not offer_id or len(offer_id) > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Артикул: от 1 до 100 символов")
    history = await svc.load_cost_history(db, user.id)
    record, created = await svc.add_cost(
        db, user.id, offer_id, data.cost_price, data.vat_multiplier, data.note, data.effective_from, history=history
    )
    await db.flush()
    recosted = 0
    if created:
        recosted = await recost_reports(db, user.id, [offer_id])
        start, end = affected_period(history.dates(offer_id), record.effective_from)
        await invalidate(db, user.id, start, end, f"Себестоимость {offer_id}")
    await db.commit()
    history = await svc.load_cost_history(db, user.id)
    info = (await svc.known_articles(db, user.id, history)).get(offer_id) or svc.ArticleInfo(offer_id)
    return CostSaved(item=_row(info, history), created=created, recosted_reports=recosted)


@router.get("/{offer_id:path}/history", response_model=list[CostRecord])
async def cost_history(offer_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    history = await svc.load_cost_history(db, user.id)
    records = history.history(offer_id)
    if not records:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Себестоимость для этого артикула ещё не задавалась")
    return [
        CostRecord(
            cost_price=_q(r.cost_price, "0.01"),
            vat_multiplier=_q(r.vat_multiplier, "0.001"),
            effective_from=r.effective_from,
            note=r.note,
            source=r.source,
            created_at=r.created_at,
        )
        for r in reversed(records)  # новые — первыми
    ]


async def _read_upload(file: UploadFile) -> bytes:
    limit = MAX_FILE_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Файл больше {MAX_FILE_MB} МБ")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    return content


@router.post("/import", response_model=ImportResponse)
async def import_file(
    request: Request,
    file: UploadFile = File(
        ..., description="CSV или XLSX: артикул, себестоимость без НДС, коэффициент НДС, комментарий"
    ),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — применить"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if dry_run:
        audit.not_a_change(request)
    content = await _read_upload(file)
    try:
        result = await import_costs(db, user.id, content, file.filename or "", apply=not dry_run)
    except CostImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ImportResponse(
        applied=result.applied,
        counts=ImportCounts(**{s: result.count(s) for s in ("new", "changed", "unchanged", "skipped", "error")}),
        recosted_reports=result.recosted_reports,
        rows=[ImportRowOut.model_validate(r, from_attributes=True) for r in result.rows],
    )


@router.post("/import/errors", response_class=Response, dependencies=[Depends(audit.read_only)])
async def import_errors_file(
    file: UploadFile = File(..., description="Тот же файл, что загружали в /import"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: исправить и загрузить снова."""
    content = await _read_upload(file)
    try:
        out, fmt = await error_file(db, user.id, content, file.filename or "")
    except CostImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _file(out, fmt, "Себестоимость — строки с ошибками", "costs-errors")
