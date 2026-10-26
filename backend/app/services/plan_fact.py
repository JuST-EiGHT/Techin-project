"""План-факт (шаг 10.20): плановые платежи календаря и операции, которые их закрыли, — и предложения «это оплата
плана …» для неоплаченных.

Сопоставление — расчёт календаря 3.9 (`plan_calendar.snapshot`: отмеченные «оплачено», найденные по сумме, дате,
статье и контрагенту, ЕНП по сроку). Предложения — `payment_calendar.suggest`: похожие операции, которые под точное
сопоставление не подошли. «Да, это оплата» — та же отметка «оплачено» с операцией (`mark_paid`), у операции без статьи
ставится статья платежа (новая версия, «вручную»). «Нет» — `plan_match_dismissals`, предложение больше не показываем.
Своих итоговых данных нет — всё пересчитывается.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payment_calendar as cal
from app.models.planning import MoneyOperation, PlanMatchDismissal
from app.services import plan_calendar
from app.services import planning as planning_svc
from app.services.planning import PlanningError, _current

ZERO = Decimal(0)
CENT = Decimal("0.01")
MAX_WINDOW_DAYS = 400


async def _dismissed(db: AsyncSession, user_id: uuid.UUID) -> set[tuple[str, str]]:
    res = await db.execute(select(PlanMatchDismissal.key, PlanMatchDismissal.op_id).where(
        PlanMatchDismissal.user_id == user_id))
    return {(k, str(o)) for k, o in res.all()}


def _money(v: Decimal | None) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(CENT)


async def overview(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    snap = await plan_calendar.snapshot(db, user_id, date_from, date_to)
    today = snap.ctx.today
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    by_code = {c.code: str(c.id) for c in cats.values() if c.code and c.archived_at is None}

    def category_of(item: cal.Item) -> str | None:
        return item.category_id or by_code.get(plan_calendar.TAX_CATEGORY.get(item.source))

    suggestions = {sg.item.key: sg for sg in cal.suggest(snap.visible, snap.ops, today, await _dismissed(db, user_id),
                                                         category_of)}
    op_ids = {uuid.UUID(i.fact.op_id) for i in snap.visible if i.fact is not None and i.fact.op_id}
    op_ids |= {uuid.UUID(sg.op.op_id) for sg in suggestions.values()}
    rows = {}
    if op_ids:
        res = await db.execute(_current(select(MoneyOperation).where(
            MoneyOperation.user_id == user_id, MoneyOperation.op_id.in_(op_ids))))
        rows = {r.op_id: r for r in res.scalars()}
    accounts = {a.id: a.name for a in await planning_svc._accounts(db, user_id)}

    def cat_name(cid) -> str | None:
        c = cats.get(uuid.UUID(cid) if isinstance(cid, str) else cid) if cid else None
        return c.name if c else None

    def op_out(op_id: str | None, fallback: cal.Op | None = None) -> dict | None:
        row = rows.get(uuid.UUID(op_id)) if op_id else None
        if row is None:
            if fallback is None:
                return None
            return {"op_id": None, "day": fallback.day, "amount": _money(fallback.amount), "category": None,
                    "counterparty": None, "account": None, "comment": None}
        return {"op_id": row.op_id, "day": row.day, "amount": _money(row.amount), "category": cat_name(row.category_id),
                "counterparty": row.counterparty, "account": accounts.get(row.account_id), "comment": row.comment}

    out = []
    counts = {"paid": 0, "partial": 0, "overdue": 0, "expected": 0, "planned": 0, "suggestions": 0}
    totals = {"plan": ZERO, "fact": ZERO, "unpaid": ZERO}
    for item in snap.visible:
        status = item.status(today)
        if status not in counts:
            continue
        counts[status] += 1
        fact = item.fact
        how = None
        if fact is not None:
            how = "marked" if item.fact_marked else ("tax" if item.source == "tax" else "auto")
        sg = suggestions.get(item.key)
        if sg is not None:
            counts["suggestions"] += 1
        if item.amount is not None:
            totals["plan"] += item.amount
            if fact is None:
                totals["unpaid"] += item.amount
        if fact is not None:
            totals["fact"] += fact.amount
        out.append({
            "key": item.key, "title": item.title, "source": item.source, "kind": item.kind, "day": item.day,
            "amount": _money(item.amount), "estimate": item.estimate, "category": cat_name(category_of(item)),
            "counterparty": item.counterparty, "status": status, "how": how,
            "fact": op_out(fact.op_id, fact) if fact is not None else None,
            "diff": _money(item.diff) if fact is not None and not item.meta.get("shared_fact") else None,
            "days_late": (fact.day - item.day).days if fact is not None else None,
            "suggestion": None if sg is None else {
                "op": op_out(sg.op.op_id), "reasons": sg.reasons,
                "category_fix": cat_name(category_of(item)) if not sg.op.category_id and category_of(item) else None,
            },
        })
    return {"date_from": date_from, "date_to": date_to, "today": today, "rows": out, "counts": counts,
            "totals": {k: _money(v) for k, v in totals.items()}}


async def accept(db: AsyncSession, user_id: uuid.UUID, key: str, op_id: uuid.UUID) -> dict:
    """«Да, это оплата»: платёж отмечен оплаченным этой операцией; у операции без статьи — статья платежа. Коммит —
    у вызывающего."""
    op = await planning_svc.current_operation(db, user_id, op_id)
    ctx = await plan_calendar.context(db, user_id)
    item = await plan_calendar.find_item(ctx, key)
    if (op.amount > 0) != (item.kind == "income"):
        raise PlanningError("Поступление не может быть оплатой платежа, а платёж — поступлением")
    await plan_calendar.mark_paid(db, user_id, key, op_id=op_id)
    category = item.meta.get("category_id") or await plan_calendar._category_by_code(
        ctx, plan_calendar.TAX_CATEGORY.get(item.source))
    named = None
    if op.category_id is None and category is not None and op.transfer_id is None:
        db.add(planning_svc._new_version(op, category_id=category, category_by="user", rule_id=None))
        cat = await planning_svc._category(db, user_id, category)
        named = cat.name
    await db.flush()
    return {"title": item.title, "category_set": named}


async def dismiss(db: AsyncSession, user_id: uuid.UUID, key: str, op_id: uuid.UUID) -> None:
    """«Нет, это не оплата этого платежа» — запись; повторное «нет» новой записи не добавляет."""
    await planning_svc.current_operation(db, user_id, op_id)
    if (key, str(op_id)) not in await _dismissed(db, user_id):
        db.add(PlanMatchDismissal(user_id=user_id, key=key[:120], op_id=op_id))
        await db.flush()
