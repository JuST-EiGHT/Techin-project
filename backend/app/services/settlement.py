"""«Выплаты и расчёты с Ozon» (шаг 10.23): расчёты за период — баланс у Ozon (на начало, начислено, выплачено, на
конец; 3.6), начисления и удержания по статьям (P&L 3.2, строками `engine/pnl_statement`), выплаты Ozon и поступления
в банк по каждой выплате (3.11, `engine/settlement`), расхождения с объяснением, отчёты о взаиморасчётах (10.12).

Сверка с банком — та же, что у аномалий 7.1 (`bank.ozon_check` строится на `reconcile` отсюда). Нет данных — null, а не
0: дни баланса не загружены — пропуск; выписок нет — сверки нет.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payouts as payout_engine
from app.engine import pnl_statement
from app.engine import settlement as eng
from app.models.bank import BankStatement, BankTransaction
from app.models.planning import MoneyOperation
from app.models.report import OzonDocument
from app.services import ozon_payouts, pnl_service
from app.services.planning import OZON_KEY, _current
from app.services.sync.runner import latest_run
from app.services.user_settings import anomaly_thresholds, get_settings

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _q(v) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(CENT)


async def receipts(db: AsyncSession, user_id: uuid.UUID, lo: date, hi: date) -> list[eng.Receipt]:
    """Поступления от Ozon в выписках за дни lo…hi; выплата, с которой связала загрузка, — по ключу операции."""
    txs = list((await db.execute(select(BankTransaction).where(
        BankTransaction.user_id == user_id, BankTransaction.kind == "ozon",
        BankTransaction.day >= lo, BankTransaction.day <= hi,
    ))).scalars())
    ops = {o.op_id: o for o in (await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.op_id.in_([t.op_id for t in txs if t.op_id]),
    )))).scalars()} if txs else {}
    out = []
    for t in txs:
        op = ops.get(t.op_id)
        key = op.external_key if op is not None else None
        linked = date.fromisoformat(key[len(OZON_KEY):]) if key and key.startswith(OZON_KEY) else None
        out.append(eng.Receipt(str(t.id), t.day, Decimal(t.amount), linked, str(t.account_id),
                               str(t.op_id) if t.op_id else None))
    return out


async def bank_state(db: AsyncSession, user_id: uuid.UUID) -> tuple[bool, date | None, date | None]:
    """Есть ли выписки вообще и за какие дни: с начала первой выписки (или первой операции банка по API) по последнюю
    операцию."""
    lo, hi = (await db.execute(select(func.min(BankTransaction.day), func.max(BankTransaction.day)).where(
        BankTransaction.user_id == user_id))).one()
    first = await db.scalar(select(func.min(BankStatement.date_from)).where(BankStatement.user_id == user_id))
    if first is not None and (lo is None or first < lo):
        lo = first
    return hi is not None, lo, hi


async def reconcile(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date,
                    payouts: list[eng.Payout]) -> tuple[list[eng.Row], bool, date | None]:
    """Сверка выплат периода с поступлениями (окно — до 5 дней после конца периода); поступления без выплаты — только
    за сам период."""
    has_bank, since, until = await bank_state(db, user_id)
    got = await receipts(db, user_id, date_from, date_to + timedelta(days=eng.WINDOW_DAYS)) if has_bank else []
    rows = eng.reconcile(payouts, got, until, has_bank, bank_from=since)
    rows = [r for r in rows if not (r.status == eng.BANK_ONLY and r.receipt.day > date_to)]  # type: ignore[union-attr]
    return rows, has_bank, until


def _row_out(r: eng.Row) -> dict:
    p, b = r.payout, r.receipt
    return {
        "status": r.status, "linked": r.linked,
        "payout_day": p.day if p else None, "ozon_amount": _q(p.amount) if p else None,
        "expected": _q(p.expected) if p else None,
        "week_from": p.week_from if p else None,
        "week_to": p.week_from + timedelta(days=6) if p and p.week_from else None,
        "schedule_diff": _q(p.amount - p.expected) if p and p.expected is not None else None,
        "bank_day": b.day if b else None, "bank_amount": _q(b.amount) if b else None, "diff": r.diff,
        "account_id": b.account_id if b else None, "op_id": b.op_id if b else None,
    }


def _issue_out(i: eng.Issue) -> dict:
    return {
        "kind": i.kind, "severity": i.severity, "day": i.day, "title": i.title, "text": i.text, "amount": _q(i.amount),
        "payout_day": i.payout_day, "bank_day": i.bank_day, "week_from": i.week_from,
        "account_id": i.receipt.account_id if i.receipt else None, "op_id": i.receipt.op_id if i.receipt else None,
    }


def reliable(s: payout_engine.Schedule) -> bool:
    """График надёжен для сверки «выплата ↔ ожидаемая» — как в аномалиях 7.1."""
    return s.mode == "weekly" and s.lag_weeks is not None and not (s.source == "auto" and s.confidence == "low")


async def settlement(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    st = await ozon_payouts.load_state(db, user_id)
    s = st.schedule
    lo = payout_engine.monday(date_from) - timedelta(weeks=payout_engine.MAX_LAG + 1)
    days = await ozon_payouts.load_days(db, user_id, lo, date_to)
    weeks = ozon_payouts.weekly_accrued(days)

    payouts = []
    for d, r in sorted(days.items()):
        if date_from <= d <= date_to and r.payments:
            exp = payout_engine.expected(s, d, weeks)
            payouts.append(eng.Payout(d, abs(Decimal(r.payments)), exp[1] if exp else None, exp[0] if exp else None))
    rows, has_bank, until = await reconcile(db, user_id, date_from, date_to, payouts)

    # баланс у Ozon за период: на начало, начислено, выплачено, на конец (3.6)
    loaded = [r for d, r in sorted(days.items()) if date_from <= d <= date_to]
    total_days = (date_to - date_from).days + 1
    balance = None
    if loaded:
        first, last = days.get(date_from), loaded[-1]
        balance = {
            "opening": _q(first.opening) if first else None, "opening_day": date_from if first else loaded[0].day,
            "accrued": _q(sum((Decimal(r.accrued or 0) for r in loaded), ZERO)),
            "paid": _q(sum((abs(Decimal(r.payments)) for r in loaded), ZERO)),
            "closing": _q(last.closing), "closing_day": last.day,
            "days_total": total_days, "missing_days": total_days - len(loaded),
        }
        if first is None:
            balance["opening"] = _q(loaded[0].opening)

    # начисления и удержания по статьям — P&L периода
    accruals = None
    pnl = await pnl_service.get_pnl(db, user_id, pnl_service.PnLRequest(date_from, date_to))
    if pnl["pnl"]:
        lines = pnl_statement.ozon_rows({k: Decimal(v) for k, v in pnl["by_type"].items()})
        accruals = {
            "rows": [{"key": r.key, "title": r.title, "amount": _q(r.amount),
                      "types": [{"name": n, "amount": _q(a)} for n, a in r.types]} for r in lines],
            "total": _q(pnl["auto_total"]), "data_source": pnl["data_source"],
            "missing_days": pnl["coverage"]["missing_days"],
        }

    overdue = {}
    for d in st.overdue():
        if date_from <= d <= date_to:
            exp = payout_engine.expected(s, d, weeks)
            overdue[d] = exp[1] if exp else None
    run = await latest_run(db, user_id, "balance", status="success")
    mismatches = [m for m in ((run.stats or {}).get("mismatches", []) if run else [])
                  if date_from <= eng._period_end(m["period"]) <= date_to + timedelta(days=15)]
    th = anomaly_thresholds(await get_settings(db, user_id))
    found = eng.issues(rows, overdue, Decimal(th.payout_diff), reliable(s), mismatches)

    docs = (await db.execute(select(OzonDocument).where(
        OzonDocument.user_id == user_id, OzonDocument.doc_type == "mutual_settlement", OzonDocument.status == "ready",
        OzonDocument.period_start <= date_to, OzonDocument.period_end >= date_from,
    ).order_by(OzonDocument.period_start.desc(), OzonDocument.requested_at.desc()))).scalars()

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    paid = sum((p.amount for p in payouts), ZERO)
    received = sum((r.receipt.amount for r in rows if r.receipt is not None), ZERO)
    return {
        "date_from": date_from, "date_to": date_to,
        "has_balance": bool(days), "balance": balance, "accruals": accruals,
        "accrued_diff": (_q(balance["accrued"] - accruals["total"]) if balance and accruals
                         and balance["missing_days"] == 0 and accruals["missing_days"] == 0 else None),
        "schedule": {"title": s.title, "reliable": reliable(s)},
        "payouts": [_row_out(r) for r in rows],
        "totals": {"paid": _q(paid) if payouts else None, "received": _q(received) if has_bank else None,
                   "count": len(payouts)},
        "has_bank": has_bank, "bank_until": until, "counts": counts,
        "issues": [_issue_out(i) for i in found],
        "documents": [{"id": d.id, "period_start": d.period_start, "period_end": d.period_end, "file_name": d.file_name}
                      for d in docs],
    }
