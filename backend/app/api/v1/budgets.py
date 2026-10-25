"""Бюджеты и сценарии (шаг 10.21): план по статьям на месяц, план-факт по месяцам, кварталам и году, правка с
версиями, заполнение по факту, шаблон Excel, сценарии и прогноз денег с риском разрыва."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _fail, _file, _read_upload
from app.db.session import get_db
from app.models.user import User
from app.schemas.planning import (
    BudgetChanged,
    BudgetFillIn,
    BudgetFillOut,
    BudgetPage,
    BudgetSet,
    BudgetVersion,
    ForecastOut,
    ImportCounts,
    ImportResponse,
    ScenarioIn,
)
from app.services import audit, budget_import, budgets
from app.services import planning as planning_svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/planning/budgets", tags=["planning"])


@router.get("", response_model=BudgetPage)
async def budget_page(
    year: int | None = Query(None, ge=2000, le=2100, description="По умолчанию — текущий"),
    granularity: Literal["month", "quarter", "year"] = "month",
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """План-факт по статьям бюджета: месяцы, кварталы или год; отклонения в ₽ и %; статья — вместе с подстатьями."""
    await planning_svc.sync_ozon_operations(db, user.id)
    return await budgets.view(db, user.id, year or today_msk().year, granularity)


@router.put("/plan", response_model=BudgetChanged)
async def set_plan(data: BudgetSet, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """План статьи на месяц, квартал или год (поровну по месяцам); пусто — убрать. Прежний — в истории."""
    try:
        changed = await budgets.set_plan(db, user.id, data.category_id, data.granularity, data.start, data.amount)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return BudgetChanged(changed=changed)


@router.get("/history", response_model=list[BudgetVersion])
async def plan_history(category_id: uuid.UUID, month: date, user: User = Depends(get_current_store),
                       db: AsyncSession = Depends(get_db)):
    """Версии плана статьи на месяц, новые сверху."""
    try:
        return await budgets.history(db, user.id, category_id, month)
    except planning_svc.PlanningError as exc:
        raise _fail(exc) from exc


@router.post("/fill", response_model=BudgetFillOut, response_model_by_alias=True)
async def fill(request: Request, data: BudgetFillIn, user: User = Depends(get_current_store),
               db: AsyncSession = Depends(get_db)):
    """Заполнить план периода по факту: как в прошлом году или как прошлый период. dry_run — только показать."""
    if data.dry_run:
        audit.not_a_change(request)
    try:
        result = await budgets.fill(db, user.id, data.source, data.granularity, data.start, data.overwrite, data.dry_run)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    if not data.dry_run:
        await db.commit()
    return result


@router.get("/forecast", response_model=ForecastOut)
async def forecast(months: int = Query(3, ge=1, le=12), user: User = Depends(get_current_store),
                   db: AsyncSession = Depends(get_db)):
    """Прогноз денег по сценариям: остаток сейчас + календарь + остаток бюджета, × множители; кассовый разрыв."""
    await planning_svc.sync_ozon_operations(db, user.id)
    return await budgets.forecast(db, user.id, months)


@router.put("/scenarios", status_code=status.HTTP_204_NO_CONTENT)
async def update_scenarios(data: ScenarioIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Множители оптимистичного и пессимистичного сценария, %."""
    try:
        await budgets.update_scenarios(db, user.id, data.model_dump(exclude_unset=True))
    except planning_svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- шаблон ----------


@router.get("/template", response_class=Response)
async def template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    rows: Literal["all", "empty"] = Query("all", description="all — с планом года, empty — только статьи"),
    year: int | None = Query(None, ge=2000, le=2100),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Шаблон бюджета: статьи бюджета и план по месяцам года — скачал → заполнил → загрузил в /budgets/import."""
    year = year or today_msk().year
    content = await budget_import.template(db, user.id, fmt, rows == "empty", year)
    return _file(content, fmt, f"Бюджет {year}" if rows == "all" else "Бюджет — шаблон", "budget")


@router.post("/import", response_model=ImportResponse)
async def import_budget(
    request: Request,
    file: UploadFile = File(..., description="XLSX или CSV по шаблону: статья, вид, год, январь … декабрь"),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — применить"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    if dry_run:
        audit.not_a_change(request)
    content = await _read_upload(file)
    try:
        result = await budget_import.import_budget(db, user.id, content, file.filename or "", apply=not dry_run)
    except (budget_import.BudgetImportError, planning_svc.PlanningError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ImportResponse(
        applied=result.applied,
        counts=ImportCounts(**{s: result.count(s) for s in ("new", "changed", "unchanged", "skipped", "error")}),
        rows=[budget_import.row_out(r) for r in result.rows],
    )


@router.post("/import/errors", response_class=Response, dependencies=[Depends(audit.read_only)])
async def import_errors(
    file: UploadFile = File(..., description="Тот же файл, что загружали в /budgets/import"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: исправить и загрузить снова."""
    content = await _read_upload(file)
    try:
        out, fmt = await budget_import.error_file(db, user.id, content, file.filename or "")
    except budget_import.BudgetImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _file(out, fmt, "Бюджет — строки с ошибками", "budget-errors")
