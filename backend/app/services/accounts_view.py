"""«Счета и выписки» (шаг 10.20): история движения по счёту, сверка расчётного остатка с выпиской, нераспознанные
операции.

Всё производное: операции (`money_operations`), начальный остаток (`money_account_balances`), операции и остатки банка
(`bank_transactions`, `bank_balances`) не меняются. Сверка — `engine/reconcile`; в день, с которого разошлось, —
операции сервиса, которых нет в банке (внесены вручную, из файла, выплата Ozon без поступления в выписке): обычно
причина в них — дубль ручной операции или выплата в другой день. Нераспознанные — операции из банка и из файла без
статьи: правила их не разнесли.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import categorize as eng
from app.engine import reconcile as rec
from app.engine.anomaly_text import rub
from app.models.bank import BankBalance, BankTransaction
from app.models.planning import MoneyOperation
from app.services import categorization
from app.services import planning as planning_svc
from app.services.planning import _current

ZERO = Decimal(0)
CENT = Decimal("0.01")
SUSPECTS = 20
DAYS_SHOWN = 10  # последние сравнимые дни в ответе
TYPES = ("income", "expense", "transfer")


def _q(v: Decimal | None) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(CENT)


def _type(op: MoneyOperation) -> str:
    if op.transfer_id is not None:
        return "transfer"
    return "income" if op.amount > 0 else "expense"


# ---------- история по счёту ----------


async def history(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID, date_from: date, date_to: date,
                  kind: str | None = None) -> dict:
    """Поступления, списания и переводы по счёту, новые сверху; у каждой — остаток после неё (до начального остатка
    счёта — неизвестен). Остаток на начало и конец периода, итоги без переводов и переводы отдельно."""
    acc = await planning_svc._account(db, user_id, account_id, active=False)
    opening = (await planning_svc._openings(db, user_id)).get(acc.id)
    ops = list((await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.account_id == acc.id,
        MoneyOperation.day >= date_from, MoneyOperation.day <= date_to,
    )).order_by(MoneyOperation.day, MoneyOperation.created_at))).scalars())
    start = None
    if opening is not None and date_from >= opening.as_of:
        before = await db.scalar(_current(select(func.coalesce(func.sum(MoneyOperation.amount), 0)).where(
            MoneyOperation.user_id == user_id, MoneyOperation.account_id == acc.id,
            MoneyOperation.day >= opening.as_of, MoneyOperation.day < date_from)))
        start = Decimal(opening.amount) + Decimal(before)  # период раньше начального остатка — считаем с его даты
    cats = {c.id: c.name for c in await planning_svc._categories(db, user_id)}
    names = {a.id: a.name for a in await planning_svc._accounts(db, user_id)}
    legs: dict[uuid.UUID, MoneyOperation] = {}
    transfer_ids = [op.transfer_id for op in ops if op.transfer_id]
    if transfer_ids:
        for leg in (await db.execute(_current(select(MoneyOperation).where(
                MoneyOperation.user_id == user_id, MoneyOperation.transfer_id.in_(transfer_ids),
                MoneyOperation.account_id != acc.id)))).scalars():
            legs[leg.transfer_id] = leg

    running = start
    rows = []
    totals = {"income": ZERO, "expense": ZERO, "transfer_in": ZERO, "transfer_out": ZERO}
    for op in ops:
        amount = Decimal(op.amount)
        before_opening = opening is None or op.day < opening.as_of
        if not before_opening:
            if running is None:  # первый день с начальным остатком внутри периода
                running = Decimal(opening.amount)
            running += amount
        t = _type(op)
        if t == "transfer":
            totals["transfer_in" if amount > 0 else "transfer_out"] += amount
        else:
            totals[t] += amount
        other = legs.get(op.transfer_id) if op.transfer_id else None
        rows.append({
            "op_id": op.op_id, "day": op.day, "amount": _q(amount), "type": t,
            "category": cats.get(op.category_id) if t != "transfer" else None,
            "counterparty": op.counterparty, "comment": op.comment, "source": op.source,
            "transfer_account": names.get(other.account_id) if other else None,
            "balance": None if before_opening else _q(running), "before_opening": before_opening,
        })
    shown = [r for r in rows if kind is None or r["type"] == kind]
    closing = (await planning_svc.balances(db, user_id, date_to)).get(acc.id)
    return {
        "account": {"id": acc.id, "name": acc.name}, "date_from": date_from, "date_to": date_to,
        "opening": _q(start), "closing": closing,
        "opening_as_of": opening.as_of if opening else None,
        "totals": {k: _q(v) for k, v in totals.items()},
        "items": list(reversed(shown)),
    }


# ---------- сверка с выпиской ----------


async def _day_sums(db: AsyncSession, model, user_id: uuid.UUID, account_id: uuid.UUID, current: bool) -> dict[date, Decimal]:
    q = select(model.day, func.sum(model.amount)).where(model.user_id == user_id, model.account_id == account_id)
    if current:
        q = _current(q)
    return {d: Decimal(v) for d, v in (await db.execute(q.group_by(model.day))).all()}


def _hint(r: rec.Result, adjacent: bool) -> str:
    if r.status == "no_bank":
        return "Нет данных банка: загрузите выписку или подключите банк — будет с чем сверить"
    if r.status == "no_opening":
        return "Укажите начальный остаток счёта — без него расчётный остаток не посчитать"
    if r.status == "no_overlap":
        return "Выписка — раньше начального остатка счёта: сверить не с чем"
    if r.status == "ok":
        return f"Сходится с банком на {r.last.day:%d.%m.%Y}"
    more = "больше" if r.last.diff > 0 else "меньше"
    head = f"По сервису на {rub(abs(r.last.diff))} {more}, чем в банке на {r.last.day:%d.%m.%Y}"
    if r.from_start:
        return (f"{head}. Расходится с первого дня, за который есть данные банка ({r.since:%d.%m.%Y}): проверьте начальный "
                "остаток счёта")
    when = (f"с {r.since:%d.%m.%Y}" if adjacent
            else f"между {r.matched_until:%d.%m.%Y} и {r.since:%d.%m.%Y} (за эти дни выписки нет)")
    return f"{head}. Сходилось по {r.matched_until:%d.%m.%Y}, расходится {when}"


async def reconcile(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """По каждому действующему счёту: статус сверки, остаток банка и сервиса на последний день с данными банка,
    расхождение, с какого дня, подсказка и операции сервиса без пары в банке в день расхождения."""
    openings = await planning_svc._openings(db, user_id)
    cats = {c.id: c.name for c in await planning_svc._categories(db, user_id)}
    out = []
    for acc in await planning_svc._accounts(db, user_id):
        if acc.archived_at is not None:
            continue
        res = await db.execute(select(BankBalance.day, BankBalance.kind, BankBalance.amount).where(
            BankBalance.user_id == user_id, BankBalance.account_id == acc.id).order_by(BankBalance.created_at))
        points = rec.points_from([tuple(r) for r in res.all()])
        opening = openings.get(acc.id)
        result = rec.reconcile(
            points, await _day_sums(db, BankTransaction, user_id, acc.id, current=False),
            (opening.as_of, Decimal(opening.amount)) if opening else None,
            await _day_sums(db, MoneyOperation, user_id, acc.id, current=True),
        )
        adjacent = bool(result.since and result.matched_until and (result.since - result.matched_until).days == 1)
        suspects = []
        if result.status == "diff":
            lo = result.matched_until + timedelta(days=1) if result.matched_until else result.since
            ops = (await db.execute(_current(select(MoneyOperation).where(
                MoneyOperation.user_id == user_id, MoneyOperation.account_id == acc.id, MoneyOperation.source != "bank",
                MoneyOperation.day >= lo, MoneyOperation.day <= result.since,
            )).order_by(MoneyOperation.day, MoneyOperation.created_at).limit(SUSPECTS))).scalars()
            suspects = [{"op_id": op.op_id, "day": op.day, "amount": _q(op.amount), "source": op.source,
                         "counterparty": op.counterparty, "comment": op.comment, "category": cats.get(op.category_id),
                         "likely": result.jump is not None and Decimal(op.amount) == result.jump}
                        for op in ops]
        last = result.last
        out.append({
            "account": {"id": acc.id, "name": acc.name, "number": acc.number, "source": acc.source},
            "status": result.status, "hint": _hint(result, adjacent),
            "day": last.day if last else None, "bank": last.bank if last else None,
            "service": last.service if last else None, "diff": last.diff if last else None,
            "since": result.since, "matched_until": result.matched_until, "from_start": result.from_start,
            "adjacent": adjacent, "jump": _q(result.jump), "suspects": suspects,
            "days": [{"day": d.day, "bank": d.bank, "service": d.service, "diff": d.diff, "point": d.point}
                     for d in result.days[-DAYS_SHOWN:]],
        })
    return out


# ---------- нераспознанные операции ----------


async def unrecognized(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID | None = None,
                       limit: int = 200) -> dict:
    """Операции из банка и из файла без статьи (правила не разнесли), новые сверху: с назначением и ИНН из банка и
    заготовкой правила (ИНН или контрагент) — чтобы такие дальше разносились сами."""
    q = _current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.category_id.is_(None), MoneyOperation.transfer_id.is_(None),
        MoneyOperation.source.in_(("bank", "import")),
    ))
    if account_id is not None:
        q = q.where(MoneyOperation.account_id == account_id)
    ops = list((await db.execute(q.order_by(MoneyOperation.day.desc(), MoneyOperation.created_at.desc()))).scalars())
    facts = await categorization._facts(db, user_id, ops[:limit])
    names = {a.id: a.name for a in await planning_svc._accounts(db, user_id)}
    by_account: dict[uuid.UUID, int] = {}
    for op in ops:
        by_account[op.account_id] = by_account.get(op.account_id, 0) + 1
    items = []
    for op in ops[:limit]:
        f = facts[op.op_id]
        items.append({
            "op_id": op.op_id, "day": op.day, "amount": _q(op.amount), "type": _type(op),
            "account": {"id": op.account_id, "name": names.get(op.account_id, "—")}, "source": op.source,
            "counterparty": f.counterparty, "inn": f.inn, "purpose": f.purpose,
            "rule": {"direction": "in" if op.amount > 0 else "out", "counterparty_inn": f.inn,
                     "counterparty": None if f.inn else (f.counterparty if eng.norm(f.counterparty) else None)},
        })
    return {"total": len(ops), "items": items,
            "by_account": [{"id": k, "name": names.get(k, "—"), "count": v} for k, v in by_account.items()]}
