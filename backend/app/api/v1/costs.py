"""Справочник себестоимости: список артикулов, правка с историей, импорт CSV/XLSX (шаг 2.3)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services import costs as svc
from app.services.auth import get_current_user
from app.services.cost_import import CostImportError, import_costs
from app.services.report_costs import recost_reports
from app.services.sync.finance import today_msk

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
    missing_count = sum(r.cost_price is None for r in rows)
    if missing:
        rows = [r for r in rows if r.cost_price is None]
    if search and (q := search.strip().lower()):
        rows = [r for r in rows if q in r.offer_id.lower() or q in (r.name or "").lower()]
    # без себестоимости, но продавались — первыми: их надо заполнить в первую очередь
    rows.sort(key=lambda r: (r.cost_price is not None, -r.report_qty, r.offer_id))
    return CostsPage(items=rows, total=len(rows), missing_count=missing_count)


@router.put("/{offer_id:path}", response_model=CostSaved)
async def set_cost(
    offer_id: str, data: CostUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Новая цена — новая запись в истории; прошлые записи не меняются."""
    offer_id = offer_id.strip()
    if not offer_id or len(offer_id) > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Артикул: от 1 до 100 символов")
    _, created = await svc.add_cost(
        db, user.id, offer_id, data.cost_price, data.vat_multiplier, data.note, data.effective_from
    )
    await db.flush()
    recosted = await recost_reports(db, user.id, [offer_id]) if created else 0
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


@router.post("/import", response_model=ImportResponse)
async def import_file(
    file: UploadFile = File(
        ..., description="CSV или XLSX: артикул, себестоимость без НДС, коэффициент НДС, комментарий"
    ),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — применить"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = MAX_FILE_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Файл больше {MAX_FILE_MB} МБ")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    try:
        result = await import_costs(db, user.id, content, file.filename or "", apply=not dry_run)
    except CostImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ImportResponse(
        applied=result.applied,
        counts=ImportCounts(**{s: result.count(s) for s in ("new", "changed", "unchanged", "error")}),
        recosted_reports=result.recosted_reports,
        rows=[ImportRowOut(**vars(r)) for r in result.rows],
    )
