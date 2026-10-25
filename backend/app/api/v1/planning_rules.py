"""Правила разнесения операций по статьям и массовая разметка (шаг 3.12); журнал применения, проверка и применение
всех правил, план-факт (шаг 10.20)."""
import uuid

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _fail
from app.db.session import get_db
from app.models.user import User
from app.schemas.planning import (
    CategorizeIn,
    CategorizeOut,
    PlanFactAccepted,
    PlanFactIn,
    PlanFactPage,
    RuleIn,
    RuleOrder,
    RulePatch,
    RulePreview,
    RuleRunOut,
    RuleSaved,
    RulesApplied,
    RulesDryRun,
    RulesPage,
)
from app.services import audit, plan_fact
from app.services import categorization as svc
from app.services import planning as planning_svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/planning", tags=["planning"])


async def _page(db: AsyncSession, user: User) -> RulesPage:
    return RulesPage(builtin=svc.BUILTIN, rules=await svc.rules_out(db, user.id))


async def _saved(db: AsyncSession, user: User, rule_id: uuid.UUID | None, applied: int) -> RuleSaved:
    rule = next((r for r in await svc.rules_out(db, user.id) if r["id"] == rule_id), None)
    return RuleSaved(rule=rule, applied=applied)


@router.get("/rules", response_model=RulesPage)
async def list_rules(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Встроенные правила (всегда первыми) и ваши — по порядку: срабатывает первое подошедшее."""
    return await _page(db, user)


@router.post("/rules", response_model=RuleSaved, status_code=status.HTTP_201_CREATED)
async def create_rule(data: RuleIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Новое правило — в конец списка; сразу разносит операции без ручной разметки."""
    try:
        rule = await svc.create_rule(db, user.id, svc.RuleIn(**data.model_dump()))
        applied = await svc.apply_rules(db, user.id, trigger="rule_created", rule_id=rule.id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return await _saved(db, user, rule.id, applied)


@router.post("/rules/preview", response_model=RulePreview, dependencies=[Depends(audit.read_only)])
async def preview_rule(data: RuleIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Сколько операций без ручной разметки подойдёт под условия — до сохранения."""
    try:
        return await svc.preview(db, user.id, svc.RuleIn(**data.model_dump()))
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc


@router.get("/rules/runs", response_model=list[RuleRunOut])
async def rule_runs(limit: int = Query(50, ge=1, le=200), user: User = Depends(get_current_store),
                    db: AsyncSession = Depends(get_db)):
    """Журнал применения правил, новые сверху: почему пересчитали, сколько проверили и изменили, какие операции (было →
    стало), ошибки и предупреждения по правилам."""
    return await svc.runs(db, user.id, limit)


@router.post("/rules/check", response_model=RulesDryRun, dependencies=[Depends(audit.read_only)])
async def check_rules(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Проверка перед массовым применением: что поменяют правила у всех операций без ручной разметки. Ничего не пишет."""
    return await svc.dry_run(db, user.id)


@router.post("/rules/apply", response_model=RulesApplied)
async def apply_all_rules(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Применить правила ко всем операциям без ручной разметки — после проверки. Запись в журнале применения."""
    changed = await svc.apply_rules(db, user.id, trigger="manual")
    await db.commit()
    return RulesApplied(changed=changed)


@router.post("/rules/reorder", response_model=RulesPage)
async def reorder_rules(data: RuleOrder, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Новый порядок правил (все id по одному разу) — и пересчёт операций без ручной разметки."""
    try:
        await svc.reorder(db, user.id, data.ids)
        await svc.apply_rules(db, user.id, trigger="reorder")
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return await _page(db, user)


@router.patch("/rules/{rule_id}", response_model=RuleSaved)
async def patch_rule(
    rule_id: uuid.UUID, data: RulePatch, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    try:
        await svc.update_rule(db, user.id, rule_id, data.model_dump(exclude_unset=True))
        applied = await svc.apply_rules(db, user.id, trigger="rule_changed", rule_id=rule_id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return await _saved(db, user, rule_id, applied)


@router.delete("/rules/{rule_id}", response_model=RuleSaved)
async def delete_rule(rule_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Операции, разнесённые этим правилом, получают статью следующего подошедшего правила или остаются без статьи.
    Ручная разметка не меняется."""
    try:
        await svc.delete_rule(db, user.id, rule_id)
        applied = await svc.apply_rules(db, user.id, trigger="rule_deleted", rule_id=rule_id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return RuleSaved(rule=None, applied=applied)


@router.post("/operations/categorize", response_model=CategorizeOut)
async def categorize(data: CategorizeIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Массовая разметка: выбранным операциям — статья вручную (правила её больше не трогают)."""
    try:
        changed = await svc.categorize(db, user.id, data.op_ids, data.category_id)
        if data.category_id is None:
            await svc.apply_rules(db, user.id, data.op_ids, trigger="categorize")  # статью убрали — пусть разнесут правила
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return CategorizeOut(changed=changed)



# ---------- план-факт (шаг 10.20) ----------


@router.get("/plan-fact", response_model=PlanFactPage)
async def plan_fact_page(
    date_from: date | None = Query(None, description="По умолчанию — 30 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — через 14 дней"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Плановые платежи окна и их факт: как закрыт (отмечено, по сумме и дате, ЕНП), отклонение по сумме и дате;
    у неоплаченных — предложение «это оплата плана …» с причинами."""
    today = today_msk()
    date_from = date_from or today - timedelta(days=30)
    date_to = date_to or today + timedelta(days=14)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days > plan_fact.MAX_WINDOW_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {plan_fact.MAX_WINDOW_DAYS} дней за раз")
    await planning_svc.sync_ozon_operations(db, user.id)
    return await plan_fact.overview(db, user.id, date_from, date_to)


@router.post("/plan-fact/accept", response_model=PlanFactAccepted)
async def plan_fact_accept(data: PlanFactIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Да, это оплата»: платёж отмечен оплаченным этой операцией; у операции без статьи — статья платежа."""
    try:
        result = await plan_fact.accept(db, user.id, data.key, data.op_id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return result


@router.post("/plan-fact/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def plan_fact_dismiss(data: PlanFactIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Нет»: эту операцию больше не предлагать как оплату этого платежа."""
    try:
        await plan_fact.dismiss(db, user.id, data.key, data.op_id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
