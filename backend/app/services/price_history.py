"""История изменений цен (шаг 6.4): все попытки изменить цену через сервис — применённые и нет — новые первыми.

Предпросмотры сюда не входят (это ещё не изменение). У каждой записи — чем закончилась, откат ли это (и чего), откатили
ли её, и можно ли откатить сейчас (быстрая проверка; полные правила §8 — в предпросмотре отката):
дошло до Ozon, не само откат, ещё не откачено, не старше 7 дней и цена в Ozon с тех пор та же.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.products import matches, positive
from app.models.price_change import (
    APPLIED,
    AUTO_RULE,
    BLOCKED,
    FAILED,
    MANUAL_BULK,
    QUEUED,
    REJECTED,
    UNKNOWN,
    PriceChange,
)
from app.models.product import Product
from app.schemas.sync import utc
from app.services.price_safety import ROLLBACK_MAX_AGE
from app.services.promotion_history import bounds
from app.services.sync.products import latest_prices

SINGLE, BULK, ROLLBACK, AUTO = "single", "bulk", "rollback", "auto"
KINDS = (SINGLE, BULK, ROLLBACK, AUTO)
STATUSES = (APPLIED, BLOCKED, REJECTED, FAILED, UNKNOWN, QUEUED)
NOT_APPLIED = "not_applied"  # фильтр: заблокировано, отказ или ошибка Ozon
STATUS_TITLES = {
    APPLIED: "Применено",
    BLOCKED: "Заблокировано проверкой",
    REJECTED: "Ozon отказал",
    FAILED: "Ошибка — цена не изменилась",
    UNKNOWN: "Ozon не ответил — могла измениться",
    QUEUED: "В очереди",
}
KIND_TITLES = {SINGLE: "Вручную", BULK: "Списком", ROLLBACK: "Откат", AUTO: "Правило"}


def kind_of(c: PriceChange) -> str:
    if c.rollback_of_id is not None:
        return ROLLBACK
    if c.change_type == MANUAL_BULK:
        return BULK
    if c.change_type == AUTO_RULE:
        return AUTO
    return SINGLE


def rollback_block(c: PriceChange, current: object, rolled: bool, now: datetime) -> str | None:
    """Почему откатить нельзя (коротко) или None — можно предложить откат."""
    if c.status not in (APPLIED, UNKNOWN):
        return "не дошло до Ozon"
    if c.rollback_of_id is not None:
        return "это откат — откат отката запрещён"
    if rolled:
        return "уже откатили"
    if now - utc(c.created_at) > ROLLBACK_MAX_AGE:
        return f"старше {ROLLBACK_MAX_AGE.days} дней"
    if c.price_before is None or c.price_after is None:
        return "нет прежней цены"
    if current is None or positive(current.price) != c.price_after:
        return "цена с тех пор изменилась"
    return None


def _conditions(user_id: uuid.UUID, date_from: date, date_to: date, product_id, status, kind, found) -> list:
    start, end = bounds(date_from, date_to)
    cond = [PriceChange.user_id == user_id, PriceChange.is_dry_run.is_(False),
            PriceChange.created_at >= start, PriceChange.created_at < end]
    if product_id is not None:
        cond.append(PriceChange.product_id == product_id)
    if status == NOT_APPLIED:
        cond.append(PriceChange.status.in_((BLOCKED, REJECTED, FAILED)))
    elif status is not None:
        cond.append(PriceChange.status == status)
    if kind == ROLLBACK:
        cond.append(PriceChange.rollback_of_id.is_not(None))
    elif kind is not None:
        types = {SINGLE: "manual_single", BULK: MANUAL_BULK, AUTO: AUTO_RULE}
        cond += [PriceChange.rollback_of_id.is_(None), PriceChange.change_type == types[kind]]
    if found is not None:  # поиск: товары, подошедшие по артикулу, названию или SKU
        cond.append(PriceChange.product_id.in_(found))
    return cond


async def _search(db: AsyncSession, user_id: uuid.UUID, search: str | None) -> list[uuid.UUID] | None:
    """Товары под поиск — как на странице товаров (регистр и «ё» не важны; SQLite не понижает кириллицу)."""
    if not search:
        return None
    rows = await db.execute(
        select(Product.id, Product.offer_id, Product.name, Product.ozon_sku).where(Product.user_id == user_id)
    )
    return [pid for pid, offer, name, sku in rows.all() if matches(search, offer, name, sku)]


async def history(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, *, product_id: uuid.UUID | None = None,
    status: str | None = None, kind: str | None = None, search: str | None = None, limit: int = 50, offset: int = 0,
) -> dict:
    found = await _search(db, user_id, search)
    cond = _conditions(user_id, date_from, date_to, product_id, status, kind, found)
    base = select(PriceChange, Product).join(Product, Product.id == PriceChange.product_id).where(*cond)
    total = await db.scalar(
        select(func.count()).select_from(PriceChange).join(Product, Product.id == PriceChange.product_id).where(*cond)
    ) or 0
    order = (PriceChange.created_at.desc(), PriceChange.id)
    rows = (await db.execute(base.order_by(*order).offset(offset).limit(limit))).all()
    counts_cond = _conditions(user_id, date_from, date_to, product_id, None, kind, found)
    by_status = dict((await db.execute(
        select(PriceChange.status, func.count()).join(Product, Product.id == PriceChange.product_id)
        .where(*counts_cond).group_by(PriceChange.status)
    )).all())

    ids = [c.id for c, _ in rows]
    rollbacks: dict[uuid.UUID, PriceChange] = {}
    if ids:
        for r in (await db.execute(
            select(PriceChange).where(PriceChange.rollback_of_id.in_(ids), PriceChange.is_dry_run.is_(False),
                                      PriceChange.status.in_((APPLIED, UNKNOWN)))
            .order_by(PriceChange.created_at)
        )).scalars():
            rollbacks[r.rollback_of_id] = r
    originals = {o.id: o for o in (await db.execute(
        select(PriceChange).where(PriceChange.id.in_([c.rollback_of_id for c, _ in rows if c.rollback_of_id]))
    )).scalars()} if any(c.rollback_of_id for c, _ in rows) else {}
    current = await latest_prices(db, list({p.id for _, p in rows}))
    now = datetime.now(timezone.utc)
    items = []
    for c, p in rows:
        rolled = rollbacks.get(c.id)
        why = rollback_block(c, current.get(p.id), rolled is not None, now)
        pct = None
        if c.price_before and c.price_after is not None:
            pct = ((c.price_after - c.price_before) / c.price_before * 100).quantize(Decimal("0.1"))
        orig = originals.get(c.rollback_of_id) if c.rollback_of_id else None
        items.append({
            "id": c.id, "created_at": utc(c.created_at),
            "product": {"id": p.id, "offer_id": c.offer_id, "name": p.name},
            "kind": kind_of(c), "kind_title": KIND_TITLES[kind_of(c)], "reason": c.reason,
            "price_before": c.price_before, "price_after": c.price_after, "change_pct": pct,
            "status": c.status, "status_title": STATUS_TITLES.get(c.status, c.status),
            "error": c.error or (" ".join(c.errors) if c.errors else None), "warnings": c.warnings or [],
            "batch_id": c.batch_id,
            "rollback_of": {"id": orig.id, "created_at": utc(orig.created_at)} if orig is not None else None,
            "rolled_back_by": {"id": rolled.id, "created_at": utc(rolled.created_at), "status": rolled.status}
            if rolled is not None else None,
            "can_rollback": why is None, "rollback_block": why,
        })
    return {
        "date_from": date_from, "date_to": date_to, "total": total, "limit": limit, "offset": offset,
        "counts": {**{s: by_status.get(s, 0) for s in STATUSES}, "all": sum(by_status.values()),
                   NOT_APPLIED: sum(by_status.get(s, 0) for s in (BLOCKED, REJECTED, FAILED))},
        "items": items,
    }
