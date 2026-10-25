"""Прочие расходы бизнеса в P&L (шаг 10.22): факт операций за период по статьям с отметкой «в P&L» (10.19).

Только при включённом «Планировании» — иначе своих операций нет и строки в P&L нет (None, а не 0). Каждая операция
считается один раз: расход — минус, возврат денег по статье расхода (плюс) уменьшает расход. Переводы между своими
счетами — не расход. Подстатья — внутри своей статьи (как в ДДС). Что не вошло — тоже в ответе, чтобы было видно почему:
расходы без статьи и расходы по статьям не «в P&L» (закупка товара — уже в себестоимости, налоги — строкой налога,
кредиты и вывод владельцу — не расход бизнеса).
"""
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.pnl_statement import Category
from app.models.planning import MoneyOperation
from app.services import planning as planning_svc

ZERO = Decimal(0)


async def expenses(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict | None:
    """Расходы бизнеса за период по статьям и что не вошло; «Планирование» выключено — None."""
    if not (await planning_svc.state(db, user_id))["enabled"]:
        return None
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    rows = (await db.execute(planning_svc._current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.day >= date_from, MoneyOperation.day <= date_to,
        MoneyOperation.transfer_id.is_(None),
    )))).scalars()
    spent: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[uuid.UUID, int] = defaultdict(int)
    loose_sum, loose_count = ZERO, 0
    for op in rows:
        cat = cats.get(op.category_id) if op.category_id else None
        if cat is None:
            if op.amount < 0 and op.source != "ozon_payout":
                loose_sum, loose_count = loose_sum - op.amount, loose_count + 1
            continue
        if cat.kind == "expense":
            spent[cat.id] -= Decimal(op.amount)
            counts[cat.id] += 1

    included = {cid for cid in spent if cats[cid].in_pnl}
    tree: dict[uuid.UUID, Category] = {}
    for cid in sorted(included, key=lambda c: (cats[c].parent_id is not None, cats[c].name)):
        cat = cats[cid]
        top = cats.get(cat.parent_id) if cat.parent_id else None
        if top is None:
            node = tree.setdefault(cid, Category(str(cid), cat.name, ZERO))
            node.amount += spent[cid]
            continue
        parent = tree.setdefault(top.id, Category(str(top.id), top.name, ZERO))
        parent.amount += spent[cid]
        parent.children.append(Category(str(cid), cat.name, spent[cid]))
    categories = sorted(tree.values(), key=lambda c: (-c.amount, c.name))
    for c in categories:
        c.children.sort(key=lambda x: (-x.amount, x.name))
    excluded = sorted(
        ({"id": str(cid), "name": cats[cid].name, "amount": spent[cid]} for cid in spent if cid not in included and spent[cid]),
        key=lambda x: (-x["amount"], x["name"]),
    )
    return {
        "total": sum((c.amount for c in categories), ZERO),
        "categories": categories,
        "operations": sum(counts[c] for c in included),
        "excluded": excluded,
        "uncategorized": {"count": loose_count, "amount": loose_sum},
    }


def category_out(c: Category) -> dict:
    return {"id": c.id, "name": c.name, "amount": c.amount, "children": [category_out(x) for x in c.children]}
