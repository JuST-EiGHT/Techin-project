"""«Финансы → Долги поставщикам» (шаг 10.29): кредиторская задолженность. Только для тех, кто платит поставщику после
продажи или с отсрочкой («Откуда товар»); остальным — 403 с объяснением."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _file, _read_upload
from app.db.session import get_db
from app.models.user import User
from app.schemas.payables import EntryIn, EntryOut, EntryPatch, ImportRowOut, PayablesOut, SetupIn
from app.schemas.planning import ImportCounts
from app.services import audit
from app.services import payables as svc
from app.services import payables_import as imp
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk
from pydantic import BaseModel

router = APIRouter(prefix="/finance/payables", tags=["payables"])


class PayablesImportResponse(BaseModel):
    applied: bool
    counts: ImportCounts
    rows: list[ImportRowOut]


def _fail(exc: svc.PayablesError) -> HTTPException:
    if isinstance(exc, svc.NotPostpay):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, svc.NotFound):
        code = status.HTTP_404_NOT_FOUND
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(code, str(exc))


@router.get("", response_model=PayablesOut)
async def get_payables(
    brief: bool = Query(False, description="Только итоги — для карточки в «Обзоре финансов»"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await svc.state(db, user.id, brief=brief)
    except svc.PayablesError as exc:
        raise _fail(exc) from exc


@router.put("/setup", response_model=PayablesOut)
async def put_setup(data: SetupIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Начало учёта: с какого дня и какой долг на этот день. Повторно — новая версия (прежняя остаётся в истории)."""
    try:
        await svc.set_opening(db, user.id, data.start, data.amount, data.due_day)
        await db.commit()
        return await svc.state(db, user.id)
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc


@router.post("/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
async def add_entry(data: EntryIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Закупка с отсрочкой или оплата поставщику. Срок закупки пуст — дата закупки + отсрочка из настроек."""
    try:
        row = await svc.create_entry(db, user.id, data.kind, data.model_dump(exclude={"kind"}))
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return {**svc._entry_out(row)}


@router.patch("/entries/{entry_id}", response_model=EntryOut)
async def patch_entry(
    entry_id: uuid.UUID, data: EntryPatch, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Исправление — новая версия записи; прежняя остаётся в истории."""
    try:
        row, _ = await svc.update_entry(db, user.id, entry_id, data.model_dump(exclude_unset=True))
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return svc._entry_out(row)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_entry(entry_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Скрыть запись: из расчёта она уходит, в истории остаётся."""
    try:
        await svc.delete_entry(db, user.id, entry_id)
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc


# ---------- списком: шаблон и загрузка ----------


@router.get("/entries/template", response_class=Response)
async def entries_template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    rows: Literal["all", "empty"] = Query("all", description="all — с вашими записями, empty — пустой"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    try:
        content = await imp.template(db, user.id, fmt, rows == "empty")
    except svc.PayablesError as exc:
        raise _fail(exc) from exc
    name = "Закупки и оплаты — шаблон" if rows == "empty" else f"Закупки и оплаты {today_msk():%d.%m.%Y}"
    return _file(content, fmt, name, "payables")


@router.post("/entries/import", response_model=PayablesImportResponse)
async def import_entries(
    request: Request,
    file: UploadFile = File(..., description="XLSX или CSV по шаблону"),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — применить"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    if dry_run:
        audit.not_a_change(request)
    content = await _read_upload(file)
    try:
        result = await imp.import_entries(db, user.id, content, file.filename or "", apply=not dry_run)
    except (imp.PayablesImportError, svc.PayablesError) as exc:
        await db.rollback()
        if isinstance(exc, svc.NotPostpay):
            raise _fail(exc) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return PayablesImportResponse(
        applied=result.applied,
        counts=ImportCounts(**{s: result.count(s) for s in ("new", "changed", "unchanged", "skipped", "error")}),
        rows=[ImportRowOut(**imp.row_out(r)) for r in result.rows],
    )


@router.post("/entries/import/errors", response_class=Response, dependencies=[Depends(audit.read_only)])
async def entries_import_errors(
    file: UploadFile = File(..., description="Тот же файл, что загружали в /entries/import"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    content = await _read_upload(file)
    try:
        out, fmt = await imp.error_file(db, user.id, content, file.filename or "")
    except imp.PayablesImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except svc.PayablesError as exc:
        raise _fail(exc) from exc
    return _file(out, fmt, "Закупки и оплаты — строки с ошибками", "payables-errors")
