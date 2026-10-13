"""Отчёты Ozon (XLSX): разбор без сохранения, загрузка и история загруженных отчётов."""

import posixpath
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.integrations.xlsx import CostProvider, ReportParseError
from app.models.report import Report
from app.models.user import User
from app.schemas.report import (
    NormalizedReportData,
    ParsedReport,
    PnLOut,
    ReportConflictItem,
    ReportDetail,
    ReportItem,
    ReportsPage,
    ReportUpdate,
)
from app.services import reports as svc
from app.services.auth import get_current_user
from app.services.costs import get_cost_provider
from app.services.report_pnl import PnLContext, load_context, report_pnl, to_out

router = APIRouter(prefix="/reports", tags=["reports"])

_NOT_FOUND = "Отчёт не найден"
_XLS = "Файл в старом формате Excel (.xls). Откройте его в Excel и сохраните как .xlsx, затем загрузите снова."
_NOT_XLSX = "Поддерживаются только файлы .xlsx — отчёты из личного кабинета Ozon в формате Excel."
FILE_DESCRIPTION = "Отчёт по начислениям или отчёт о реализации Ozon, .xlsx"


def _filename(file: UploadFile) -> str:
    # некоторые браузеры присылают путь целиком — оставляем только имя
    return posixpath.basename((file.filename or "").replace("\\", "/")).strip()[:255] or "отчёт.xlsx"


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    name = _filename(file)
    lower = name.lower()
    if lower.endswith(".xls"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _XLS)
    if not lower.endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _NOT_XLSX)
    limit = settings.REPORT_MAX_SIZE_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Файл больше {settings.REPORT_MAX_SIZE_MB} МБ — это не похоже на отчёт Ozon.",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    return name, content


def _q(value: Decimal | None, places: str) -> Decimal | None:
    # одинаковый вид чисел в ответе для PostgreSQL и SQLite: масштаб колонки
    return None if value is None else Decimal(value).quantize(Decimal(places))


def _pnl(data: NormalizedReportData, ctx: PnLContext, report: Report | None = None) -> PnLOut:
    return to_out(report_pnl(data, ctx, report))


def _item(r: Report, ctx: PnLContext) -> dict:
    s = r.summary_json
    return dict(
        id=r.id,
        filename=r.filename,
        report_type=r.report_type,
        period=r.period,
        period_start=r.period_start,
        period_end=r.period_end,
        status=r.status,
        error_message=r.error_message,
        uploaded_at=r.uploaded_at,
        parsed_at=r.parsed_at,
        auto_total=Decimal(s["auto_total"]),
        manual_total=_q(r.manual_total, "0.01"),
        effective_total=_q(svc.effective_total(r), "0.01"),
        revenue_base=Decimal(s["revenue_base"]),
        cost_sales=Decimal(s["cost_sales"]),
        vat_multiplier=_q(r.vat_multiplier, "0.001"),
        tax_base=r.tax_base,
        tax_rate=_q(r.tax_rate, "0.0001"),
        rows_count=s["rows_count"],
        missing_cost_count=len(s["missing_cost"]),
        warnings_count=len(s["warnings"]),
        pnl=_pnl(NormalizedReportData.model_validate(s), ctx, r),
    )


async def _detail(db: AsyncSession, r: Report) -> ReportDetail:
    ctx = await load_context(db, r.user_id)
    return ReportDetail(
        **_item(r, ctx), file_size=r.file_size, summary=NormalizedReportData.model_validate(r.summary_json)
    )


async def _owned(db: AsyncSession, user: User, report_id: uuid.UUID, deleted: bool = False) -> Report:
    report = await svc.get_report(db, user.id, report_id, deleted=deleted)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return report


@router.post("/parse", response_model=ParsedReport)
async def parse_report_file(
    file: UploadFile = File(..., description=FILE_DESCRIPTION),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    costs: CostProvider = Depends(get_cost_provider),
):
    """Разобрать файл без сохранения: данные отчёта и P&L."""
    name, content = await _read_upload(file)
    try:
        data = (await svc.parse_file(content, costs, name)).data
    except ReportParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ParsedReport(**data.model_dump(), pnl=_pnl(data, await load_context(db, user.id)))


@router.post("/upload", response_model=ReportDetail, status_code=status.HTTP_201_CREATED)
async def upload_report(
    file: UploadFile = File(..., description=FILE_DESCRIPTION),
    on_conflict: Literal["ask", "replace", "keep"] = Query(
        "ask", description="Отчёт за этот период уже есть: ask — вернуть 409, replace — заменить, keep — оставить оба"
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    costs: CostProvider = Depends(get_cost_provider),
):
    name, content = await _read_upload(file)
    try:
        report = await svc.create_report(db, user.id, name, content, costs, on_conflict)
    except ReportParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except svc.ReportConflict as exc:
        conflicts = [
            ReportConflictItem(
                id=c.report.id,
                filename=c.report.filename,
                period=c.report.period,
                period_start=c.report.period_start,
                period_end=c.report.period_end,
                uploaded_at=c.report.uploaded_at,
                same_file=c.same_file,
            ).model_dump(mode="json")
            for c in exc.conflicts
        ]
        return JSONResponse(
            {"detail": str(exc), "code": status.HTTP_409_CONFLICT, "conflicts": conflicts},
            status_code=status.HTTP_409_CONFLICT,
        )
    return await _detail(db, report)


@router.get("", response_model=ReportsPage)
async def list_reports(
    report_type: Literal["accrual", "realization"] | None = Query(
        None, description="accrual — недельные (начисления), realization — месячные (реализация)"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reports, total = await svc.list_reports(db, user.id, report_type, limit, offset)
    ctx = await load_context(db, user.id)
    return ReportsPage(items=[ReportItem(**_item(r, ctx)) for r in reports], total=total)


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(report_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _detail(db, await _owned(db, user, report_id))


@router.patch("/{report_id}", response_model=ReportDetail)
async def update_report(
    report_id: uuid.UUID,
    data: ReportUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ручные правки: сколько реально пришло, коэффициент НДС поставщика, свой процент налога.
    null — вернуться к налоговому профилю."""
    report = await _owned(db, user, report_id)
    fields = data.model_fields_set
    if "manual_total" in fields:
        report.manual_total = data.manual_total
    for name in ("vat_multiplier", "tax_base", "tax_rate"):
        if name in fields:
            setattr(report, name, getattr(data, name))
    if fields:
        await svc.stale_pnl(db, report, "Отчёт исправлен")
    await db.commit()
    return await _detail(db, report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Скрыть отчёт: из списков и расчётов пропадает, файл остаётся — можно восстановить."""
    await svc.hide_report(db, await _owned(db, user, report_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{report_id}/restore", response_model=ReportDetail)
async def restore_report(
    report_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    costs: CostProvider = Depends(get_cost_provider),
):
    """Вернуть скрытый (удалённый или заменённый) отчёт."""
    report = await _owned(db, user, report_id, deleted=True)
    try:
        return await _detail(db, await svc.restore_report(db, report, costs))
    except ReportParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{report_id}/reparse", response_model=ReportDetail)
async def reparse_report(
    report_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    costs: CostProvider = Depends(get_cost_provider),
):
    """Перечитать сохранённый файл текущей версией парсера и себестоимости; ручные правки сохраняются."""
    report = await _owned(db, user, report_id)
    try:
        return await _detail(db, await svc.reparse_report(db, report, costs))
    except ReportParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
