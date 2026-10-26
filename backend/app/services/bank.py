"""Банк в «Планировании» (шаг 3.11): счета с реквизитами, операции и загрузки выписок, сверка выплат Ozon с
поступлениями на счёт, подсказка «сколько реально пришло» для отчёта Ozon.

Сверка — производная (пересчитывается): выплата по данным Ozon (`ozon_balance_days`) ↔ поступление от Ozon в выписке.
Расхождение суммы или выплата без поступления — в отчёт сверки (позже — аномалия, шаг 7.1).
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.settlement import Payout
from app.models.bank import BankBalance, BankStatement, BankTransaction
from app.models.planning import MoneyOperation
from app.models.report import Report
from app.services import ozon_payouts, settlement
from app.services import planning as planning_svc
from app.services.planning import OZON_KEY, _current

CENT = Decimal("0.01")


def _q(v: Decimal | None) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(CENT)


async def accounts(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Счета с реквизитами: номер, БИК, банк, последняя выписка, остаток по банку и по сервису."""
    planning = {a["id"]: a for a in await planning_svc.accounts(db, user_id)}
    last = {}
    for b in (await db.execute(select(BankBalance).where(BankBalance.user_id == user_id, BankBalance.kind == "closing")
                                .order_by(BankBalance.day, BankBalance.created_at))).scalars():
        last[b.account_id] = b  # последний по дате
    counts = dict((await db.execute(select(BankTransaction.account_id, func.count()).where(
        BankTransaction.user_id == user_id).group_by(BankTransaction.account_id))).all())
    out = []
    for acc in await planning_svc._accounts(db, user_id):
        bal = last.get(acc.id)
        out.append({
            "id": acc.id, "name": acc.name, "source": acc.source, "number": acc.number, "bik": acc.bik,
            "bank_name": acc.bank_name, "archived": acc.archived_at is not None,
            "balance": planning[acc.id]["balance"], "bank_balance": _q(bal.amount) if bal else None,
            "bank_balance_day": bal.day if bal else None, "transactions": counts.get(acc.id, 0),
        })
    return out


async def transactions(
    db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID | None, date_from: date, date_to: date,
    limit: int = 200, offset: int = 0,
) -> dict:
    q = select(BankTransaction).where(
        BankTransaction.user_id == user_id, BankTransaction.day >= date_from, BankTransaction.day <= date_to)
    if account_id is not None:
        q = q.where(BankTransaction.account_id == account_id)
    rows = list((await db.execute(q.order_by(BankTransaction.day.desc(), BankTransaction.created_at.desc()))).scalars())
    names = {a.id: a.name for a in await planning_svc._accounts(db, user_id)}
    return {
        "total": len(rows),
        "items": [
            {"id": r.id, "account": {"id": r.account_id, "name": names.get(r.account_id, "—")}, "day": r.day,
             "amount": _q(r.amount), "counterparty": r.counterparty, "counterparty_inn": r.counterparty_inn,
             "counterparty_account": r.counterparty_account, "purpose": r.purpose, "doc_number": r.doc_number,
             "kind": r.kind, "source": r.source, "op_id": r.op_id}
            for r in rows[offset:offset + limit]
        ],
    }


async def statements(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(select(BankStatement).where(BankStatement.user_id == user_id)
                             .order_by(BankStatement.uploaded_at.desc()))).scalars()
    return [
        {"id": s.id, "filename": s.filename, "date_from": s.date_from, "date_to": s.date_to, "accounts": s.accounts,
         "uploaded_at": s.uploaded_at, "stats": s.stats}
        for s in rows
    ]


# ---------- сверка: выплата по данным Ozon ↔ поступление на счёт ----------


async def ozon_check(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    """По каждой выплате и каждому поступлению от Ozon: ok — совпали; diff — сумма разошлась; no_bank — выплата есть, а в
    выписке поступления нет (выписка за эти дни загружена); waiting — выписки за эти дни ещё нет; bank_only —
    поступление есть, а в данных Ozon выплаты нет (например, ключ Ozon API не подключён). Сверка — общая с «Выплатами и
    расчётами с Ozon» (`settlement.reconcile`, шаг 10.23)."""
    days = await ozon_payouts.load_days(db, user_id, date_from, date_to)
    payouts = [Payout(d, -Decimal(r.payments)) for d, r in sorted(days.items()) if r.payments]
    rows, has_bank, _ = await settlement.reconcile(db, user_id, date_from, date_to, payouts)
    if not has_bank:
        return {"has_bank": False, "rows": [], "counts": {}}
    out = [{"payout_day": r.payout.day if r.payout else None, "ozon_amount": _q(r.payout.amount) if r.payout else None,
            "bank_day": r.receipt.day if r.receipt else None, "bank_amount": _q(r.receipt.amount) if r.receipt else None,
            "diff": r.diff, "status": r.status} for r in rows]
    counts: dict[str, int] = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"has_bank": True, "rows": out, "counts": counts}


# ---------- подсказка для отчёта Ozon: сколько реально пришло ----------


async def payout_hint(db: AsyncSession, user_id: uuid.UUID, report: Report) -> dict | None:
    """Поступления от Ozon по выписке за продажи периода отчёта: выплата за неделю продаж приходит через N недель
    (график из шага 3.6), поэтому берём выплаты, сдвинутые на эти недели. Только подсказка — сохраняется по кнопке."""
    if report.period_start is None or report.period_end is None:
        return None
    txs = list((await db.execute(select(BankTransaction).where(
        BankTransaction.user_id == user_id, BankTransaction.kind == "ozon"))).scalars())
    if not txs:
        return None
    schedule = (await ozon_payouts.payouts(db, user_id, 1))["schedule"]
    lag = (schedule["lag_weeks"] or 0) if schedule["mode"] == "weekly" else 0
    lo, hi = report.period_start + timedelta(weeks=lag), report.period_end + timedelta(weeks=lag)
    ops = {o.op_id: o for o in (await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.op_id.in_([t.op_id for t in txs if t.op_id]),
    )))).scalars()}
    picked = []
    for t in txs:
        op = ops.get(t.op_id)
        key = op.external_key if op is not None else None
        payout_day = date.fromisoformat(key[len(OZON_KEY):]) if key and key.startswith(OZON_KEY) else t.day
        if lo <= payout_day <= hi:
            picked.append(t)
    if not picked:
        return None
    amount = sum((Decimal(t.amount) for t in picked), Decimal(0)).quantize(CENT)
    days = sorted(t.day for t in picked)
    period = f"{days[0]:%d.%m.%Y}" if days[0] == days[-1] else f"с {days[0]:%d.%m.%Y} по {days[-1]:%d.%m.%Y}"
    note = f"Поступлений от Ozon: {len(picked)}, {period}" + (
        f" — выплаты за продажи этого периода по графику Ozon (через {lag} нед.)." if lag else "."
    )
    return {"amount": amount, "count": len(picked), "date_from": days[0], "date_to": days[-1], "lag_weeks": lag,
            "note": note}
