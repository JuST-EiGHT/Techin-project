"""Плановые платежи и платёжный календарь (шаг 3.9): планы с версиями, перенос и «оплачено» отдельного платежа,
календарь с прогнозом остатка и кассовыми разрывами, планы списком через шаблон."""
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _fail, _file, _read_upload
from app.db.session import get_db
from app.models.planning import PlannedPayment
from app.models.user import User
from app.schemas.plan_calendar import (
    CalendarOut,
    KeyIn,
    OverrideIn,
    PaidIn,
    PaidOut,
    Plan,
    PlanIn,
    PlanPatch,
    PlanSaved,
    PlanVersion,
)
from app.schemas.planning import ImportCounts, ImportResponse
from app.services import payment_plan as pp
from app.services import plan_calendar as svc
from app.services import plan_import
from app.services import planning as planning_svc
from app.services import user_settings as settings_svc
from app.services.auth import get_current_user
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/planning", tags=["planning"])
MAX_WINDOW_DAYS = 400


async def _names(db: AsyncSession, user: User) -> tuple[dict, dict]:
    accounts = {a.id: a.name for a in await planning_svc._accounts(db, user.id)}
    cats = {c.id: c.name for c in await planning_svc._categories(db, user.id)}
    return accounts, cats


def _ref(names: dict, key: uuid.UUID | None) -> dict | None:
    return {"id": key, "name": names[key]} if key in names else None


async def _plans_out(db: AsyncSession, user: User, rows: list[PlannedPayment]) -> list[Plan]:
    accounts, cats = await _names(db, user)
    deferral = (await settings_svc.get_settings(db, user.id)).supplier_deferral_days
    today = today_msk()
    return [
        Plan(
            plan_id=r.plan_id, version=r.version, kind=r.kind, title=r.title, amount=r.amount, period=r.period,
            day=r.day, start_date=r.start_date, end_date=r.end_date, purchase_day=r.purchase_day, enabled=r.enabled,
            category=_ref(cats, r.category_id), account=_ref(accounts, r.account_id), counterparty=r.counterparty,
            comment=r.comment, source=r.source, schedule=pp.schedule_text(r, deferral),
            next_day=pp.next_day(r, deferral, today),
        )
        for r in rows
    ]


# ---------- плановые платежи ----------


@router.get("/plans", response_model=list[Plan])
async def list_plans(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Действующие плановые платежи — разовые и регулярные."""
    return await _plans_out(db, user, await pp.plans(db, user.id))


async def _category(db: AsyncSession, user: User, data: dict, kind: str) -> str | None:
    name = data.pop("category_name", None)
    if name and data.get("category_id") is None:
        category, note = await planning_svc.resolve_category(db, user.id, name, kind)
        data["category_id"] = category.id
        return note
    return None


@router.post("/plans", response_model=PlanSaved, status_code=status.HTTP_201_CREATED)
async def create_plan(data: PlanIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Разовый или регулярный платёж (поступление). Статью можно вписать названием — как в операциях."""
    fields = data.model_dump()
    try:
        note = await _category(db, user, fields, data.kind)
        row = await pp.create_plan(db, user.id, pp.PlanIn(**fields))
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return PlanSaved(plan_id=row.plan_id, version=row.version, changed=True, category_note=note)


@router.patch("/plans/{plan_id}", response_model=PlanSaved)
async def patch_plan(
    plan_id: uuid.UUID, data: PlanPatch, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Изменение — новая версия плана, прежняя остаётся в истории. enabled: false — план не участвует в прогнозе."""
    changes = data.model_dump(exclude_unset=True)
    try:
        row = await pp.current_plan(db, user.id, plan_id)
        note = await _category(db, user, changes, changes.get("kind") or row.kind)
        saved, changed = await pp.update_plan(db, user.id, plan_id, changes)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return PlanSaved(plan_id=saved.plan_id, version=saved.version, changed=changed or note in ("created", "restored"),
                     category_note=note)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Удалить план (в истории он остаётся). Оплаченные платежи — операции — не трогаем."""
    try:
        await pp.delete_plan(db, user.id, plan_id)
    except planning_svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/plans/{plan_id}/history", response_model=list[PlanVersion])
async def plan_history(plan_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        rows = await pp.history(db, user.id, plan_id)
    except planning_svc.PlanningError as exc:
        raise _fail(exc) from exc
    accounts, cats = await _names(db, user)
    deferral = (await settings_svc.get_settings(db, user.id)).supplier_deferral_days
    return [
        PlanVersion(
            version=r.version, kind=r.kind, title=r.title, amount=r.amount, schedule=pp.schedule_text(r, deferral),
            enabled=r.enabled, category=cats.get(r.category_id), account=accounts.get(r.account_id),
            counterparty=r.counterparty, comment=r.comment, created_at=r.created_at, superseded_at=r.superseded_at,
            deleted_at=r.deleted_at,
        )
        for r in rows
    ]


# ---------- планы списком: шаблон и загрузка ----------


@router.get("/plans/template", response_class=Response)
async def plans_template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    rows: Literal["all", "empty"] = Query("all", description="all — с вашими планами, empty — пустой"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Шаблон плановых платежей со своими счетами и статьями (списком в Excel): скачал → заполнил → загрузил."""
    content = await plan_import.template(db, user.id, fmt, rows == "empty")
    name = "Плановые платежи — шаблон" if rows == "empty" else f"Плановые платежи {today_msk():%d.%m.%Y}"
    return _file(content, fmt, name, "plans")


@router.post("/plans/import", response_model=ImportResponse)
async def import_plans(
    file: UploadFile = File(..., description="XLSX или CSV по шаблону"),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — применить"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = await _read_upload(file)
    try:
        result = await plan_import.import_plans(db, user.id, content, file.filename or "", apply=not dry_run)
    except (plan_import.PlanImportError, planning_svc.PlanningError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ImportResponse(
        applied=result.applied,
        counts=ImportCounts(**{s: result.count(s) for s in ("new", "changed", "unchanged", "skipped", "error")}),
        rows=[plan_import.row_out(r) for r in result.rows],
    )


@router.post("/plans/import/errors", response_class=Response)
async def plans_import_errors(
    file: UploadFile = File(..., description="Тот же файл, что загружали в /plans/import"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: исправить и загрузить снова."""
    content = await _read_upload(file)
    try:
        out, fmt = await plan_import.error_file(db, user.id, content, file.filename or "")
    except plan_import.PlanImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _file(out, fmt, "Плановые платежи — строки с ошибками", "plans-errors")


# ---------- календарь ----------


@router.get("/calendar", response_model=CalendarOut)
async def get_calendar(
    date_from: date | None = Query(None, description="По умолчанию — сегодня"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня + горизонт из настроек"),
    horizon_days: int | None = Query(None, ge=7, le=180, description="Вместо date_to: 30, 60 или 90 дней"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """План и факт по дням: свои платежи, выплаты Ozon, оплата поставщику, налоги — у каждой строки источник;
    прогноз остатка с сегодня, кассовые разрывы (ниже нуля или порога) и «денег хватит до …»."""
    today = today_msk()
    date_from = date_from or today
    if date_to is None:
        if horizon_days is None:
            horizon_days = (await planning_svc.state(db, user.id))["horizon_days"]
        date_to = max(date_from, today) + timedelta(days=horizon_days - 1)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days > MAX_WINDOW_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {MAX_WINDOW_DAYS} дней за раз")
    await planning_svc.sync_ozon_operations(db, user.id)
    return await svc.calendar(db, user.id, date_from, date_to)


@router.post("/calendar/override", status_code=status.HTTP_204_NO_CONTENT)
async def override(data: OverrideIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Перенести отдельный платёж, задать ему свою сумму, пропустить или вернуть как по плану. Прогноз
    пересчитывается сразу — запросите календарь заново."""
    changes = data.model_dump(exclude_unset=True, exclude={"key"})
    try:
        await svc.move(db, user.id, data.key, changes)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/calendar/paid", response_model=PaidOut)
async def paid(data: PaidIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """«Оплачено»: связать платёж с уже внесённой операцией или создать операцию-факт. Факт не меняется, даже если
    план потом пересчитается: расхождение видно в календаре."""
    try:
        result = await svc.mark_paid(db, user.id, data.key, data.day, data.amount, data.account_id, data.op_id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return PaidOut(**result)


@router.post("/calendar/unpaid", status_code=status.HTTP_204_NO_CONTENT)
async def unpaid(data: KeyIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Снять отметку «оплачено». Созданная операция остаётся — её можно удалить в «Операциях»."""
    try:
        await svc.unmark_paid(db, user.id, data.key)
    except planning_svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
