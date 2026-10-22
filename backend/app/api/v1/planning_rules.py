"""Правила разнесения операций по статьям и массовая разметка (шаг 3.12)."""
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _fail
from app.db.session import get_db
from app.models.user import User
from app.schemas.planning import (
    CategorizeIn,
    CategorizeOut,
    RuleIn,
    RuleOrder,
    RulePatch,
    RulePreview,
    RuleSaved,
    RulesPage,
)
from app.services import audit
from app.services import categorization as svc
from app.services import planning as planning_svc
from app.services.stores import get_current_store

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
        applied = await svc.apply_rules(db, user.id)
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


@router.post("/rules/reorder", response_model=RulesPage)
async def reorder_rules(data: RuleOrder, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Новый порядок правил (все id по одному разу) — и пересчёт операций без ручной разметки."""
    try:
        await svc.reorder(db, user.id, data.ids)
        await svc.apply_rules(db, user.id)
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
        applied = await svc.apply_rules(db, user.id)
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
        applied = await svc.apply_rules(db, user.id)
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
            await svc.apply_rules(db, user.id, data.op_ids)  # статью убрали — пусть разнесут правила, если подходят
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return CategorizeOut(changed=changed)

