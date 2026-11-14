"""«Финансы → Долги поставщикам» (шаг 10.23a): поставки от поставщика, продажи по ним (FIFO внутри артикула), долг и
оплаты. Только для тех, кто платит поставщику после продажи («Откуда товар»); остальным — 403 с объяснением."""
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _file, _read_upload
from app.core.files import attachment
from app.db.session import get_db
from app.integrations.supplier_docs import DocError
from app.models.user import User
from app.schemas.payables import LotClose, LotPatch, PayablesOut, PaymentIn, PaymentOut, PaymentPatch, UploadOut
from app.services import audit
from app.services import supplier_lots as svc
from app.services import supplier_lots_upload as upload_svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/finance/payables", tags=["payables"])


def _fail(exc: svc.PayablesError) -> HTTPException:
    if isinstance(exc, svc.NotPostpay):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, svc.NotFound):
        code = status.HTTP_404_NOT_FOUND
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(code, str(exc))


async def _state(db: AsyncSession, user_id: uuid.UUID, brief: bool = False) -> dict:
    try:
        return await svc.state(db, user_id, brief=brief)
    except svc.PayablesError as exc:
        raise _fail(exc) from exc


@router.get("", response_model=PayablesOut)
async def get_payables(
    brief: bool = Query(False, description="Только итоги — для карточки в «Обзоре финансов»"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    return await _state(db, user.id, brief)


# ---------- поставки: документ или шаблон ----------


@router.post("/lots/upload", response_model=UploadOut)
async def upload_lots(
    request: Request,
    file: UploadFile = File(..., description="Документ поставщика PDF или шаблон «Поставки списком» XLSX / CSV"),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — сохранить"),
    sold_before: str | None = Form(None, description='JSON {"№|ГГГГ-ММ-ДД|артикул": штук} — «уже продано ранее»'),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Предпросмотр (dry_run) — что в документе, что нашлось в каталоге, цены и ошибки по строкам; затем — сохранить."""
    if dry_run:
        audit.not_a_change(request)
    try:
        await svc.require_postpay(db, user.id)
    except svc.PayablesError as exc:
        raise _fail(exc) from exc
    content = await _read_upload(file)
    try:
        given = {k: int(v) for k, v in json.loads(sold_before).items()} if sold_before else {}
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "«Уже продано ранее» — целые числа штук") from exc
    try:
        docs, fmt = upload_svc.parse_upload(content, file.filename or "")
    except DocError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    previews = await upload_svc.preview(db, user.id, docs, given)
    sales_from = await svc.sales_range(db, user.id)
    counts = {s: sum(p.status == s for p in previews) for s in ("new", "changed", "unchanged", "error")}
    applied = False
    if not dry_run:
        counts = {**counts, **await upload_svc.apply(db, user.id, previews, content, file.filename or "", fmt)}
        applied = counts.get("new", 0) + counts.get("changed", 0) > 0
    return UploadOut(applied=applied, format=fmt, counts=counts, sales_known_from=sales_from,
                     docs=[upload_svc.preview_out(p, sales_from) for p in previews])


@router.get("/lots/template", response_class=Response)
async def lots_template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    rows: Literal["all", "empty"] = Query("all", description="all — с вашими поставками, empty — пустой"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    try:
        content = await upload_svc.template(db, user.id, fmt, rows == "empty")
    except svc.PayablesError as exc:
        raise _fail(exc) from exc
    name = "Поставки — шаблон" if rows == "empty" else f"Поставки {today_msk():%d.%m.%Y}"
    return _file(content, fmt, name, "supplier-lots")


@router.patch("/lots/{lot_id}", response_model=PayablesOut)
async def patch_lot(lot_id: uuid.UUID, data: LotPatch, user: User = Depends(get_current_store),
                    db: AsyncSession = Depends(get_db)):
    """Описание и «уже продано ранее» по артикулам — новой версией поставки."""
    try:
        await svc.update_lot(db, user.id, lot_id, comment=data.comment, sold_before=data.sold_before,
                             set_comment="comment" in data.model_fields_set)
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return await _state(db, user.id)


@router.post("/lots/{lot_id}/close", response_model=PayablesOut)
async def close_lot(lot_id: uuid.UUID, data: LotClose, user: User = Depends(get_current_store),
                    db: AsyncSession = Depends(get_db)):
    """Закрыть с остатком (вернули поставщику, списали): остаток не продаётся из этой поставки и не входит в долг."""
    try:
        await svc.require_postpay(db, user.id)
        await svc.close_lot(db, user.id, lot_id, data.reason, data.comment)
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return await _state(db, user.id)


@router.post("/lots/{lot_id}/reopen", response_model=PayablesOut)
async def reopen_lot(lot_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        await svc.require_postpay(db, user.id)
        await svc.reopen_lot(db, user.id, lot_id)
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return await _state(db, user.id)


@router.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lot(lot_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Убрать поставку из учёта (загружена по ошибке): в истории она остаётся."""
    try:
        await svc.require_postpay(db, user.id)
        await svc.delete_lot(db, user.id, lot_id)
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc


@router.get("/lots/{lot_id}/document", response_class=Response)
async def lot_document(lot_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Загруженный документ поставки — как есть."""
    try:
        doc = await svc.document(db, user.id, lot_id)
    except svc.PayablesError as exc:
        raise _fail(exc) from exc
    ext = doc.file_name.rsplit(".", 1)[-1].lower() if "." in doc.file_name else "pdf"
    media = {"pdf": "application/pdf", "csv": "text/csv; charset=utf-8"}.get(
        ext, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return attachment(doc.content, media, doc.file_name, f"supplier-document.{ext}")


# ---------- оплаты, внесённые здесь ----------


def _payment_out(row) -> PaymentOut:
    return PaymentOut(payment_id=row.payment_id, day=row.day, amount=row.amount, counterparty=row.counterparty,
                      comment=row.comment, source="manual", editable=True, version=row.version)


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def add_payment(data: PaymentIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Оплата поставщику, которой нет в «Операциях». Закрывает самые старые поставки первыми."""
    try:
        row = await svc.add_payment(db, user.id, data.model_dump())
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return _payment_out(row)


@router.patch("/payments/{payment_id}", response_model=PaymentOut)
async def patch_payment(payment_id: uuid.UUID, data: PaymentPatch, user: User = Depends(get_current_store),
                        db: AsyncSession = Depends(get_db)):
    try:
        await svc.require_postpay(db, user.id)
        row = await svc.update_payment(db, user.id, payment_id, data.model_dump(exclude_unset=True))
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return _payment_out(row)


@router.delete("/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payment(payment_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        await svc.require_postpay(db, user.id)
        await svc.delete_payment(db, user.id, payment_id)
        await db.commit()
    except svc.PayablesError as exc:
        await db.rollback()
        raise _fail(exc) from exc
