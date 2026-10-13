"""Отчёты Ozon (XLSX). Пока только разбор без сохранения — история отчётов появится на шаге 2.2."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.integrations.xlsx import CostProvider, ReportParseError, parse_report
from app.models.user import User
from app.schemas.report import NormalizedReportData
from app.services.auth import get_current_user
from app.services.costs import get_cost_provider

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/parse", response_model=NormalizedReportData)
async def parse_report_file(
    file: UploadFile = File(..., description="Отчёт по начислениям или отчёт о реализации Ozon, .xlsx"),
    user: User = Depends(get_current_user),
    costs: CostProvider = Depends(get_cost_provider),
):
    limit = settings.REPORT_MAX_SIZE_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Файл больше {settings.REPORT_MAX_SIZE_MB} МБ — это не похоже на отчёт Ozon.",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    try:
        # openpyxl синхронный — разбор в пуле потоков, чтобы не держать event loop
        return await run_in_threadpool(parse_report, content, costs, file.filename)
    except ReportParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
