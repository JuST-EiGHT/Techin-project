"""Выписка банка → операции планирования (шаг 3.11). Необязательный источник факта: «Планирование» работает и без банка.

- Счёт выписки находится по номеру; нет такого — новый счёт «из банка» или выбранный пользователем (без номера).
- Повторная загрузка не создаёт дублей: ключ операции — id банка или (для файла) номер + дата + сумма + счёт контрагента.
- Операция банка становится операцией планирования (источник bank): сумма, дата и счёт — по выписке, их не правят.
- Поступление от маркетплейса — по ИНН плательщика из справочника (app/data/marketplaces.json). Поступление от Ozon —
  факт выплаты Ozon (шаг 3.6): та же операция, а не вторая — выплата по данным Ozon подтверждается выпиской.
- Перевод между своими счетами (счёт контрагента — наш) — не приход и не расход: две части с общим transfer_id.
- Похожие ручные операции (сумма, дата ± 3 дня, контрагент) не объединяются молча — предлагаем объединить.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.payment_calendar import _same_text
from app.integrations.bank.statement_1c import Document, Statement, StatementError, parse
from app.models.bank import BankBalance, BankStatement, BankTransaction, MergeDismissal
from app.models.planning import MoneyAccount, MoneyAccountBalance, MoneyOperation
from app.services import planning as planning_svc
from app.services.planning import OZON_KEY, PlanningError, _current

PAYERS_FILE = Path(__file__).resolve().parents[1] / "data" / "marketplaces.json"
MATCH_DAYS = 3  # ручная операция того же дня ± 3 дня — возможный дубль
OZON_DAYS = 5  # выплата Ozon доходит до счёта за 0–5 дней
OZON_BANK_KEY = "ozon_bank:"
CENT = Decimal("0.01")


class BankImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def payers() -> dict[str, dict]:
    """ИНН плательщика → маркетплейс (справочник — данные, не код)."""
    data = json.loads(PAYERS_FILE.read_text(encoding="utf-8"))
    return {p["inn"]: p for p in data["payers"]}


def dedup_key(doc: Document) -> str:
    """Ключ операции файла: номер + дата + сумма + счёт контрагента (у одного номера в разные дни — разные ключи)."""
    return f"1c:{doc.number or '-'}:{doc.day.isoformat()}:{doc.amount}:{doc.counterparty_account or '-'}"


def ozon_close(a: Decimal, b: Decimal) -> bool:
    """Суммы выплаты по данным Ozon и поступления на счёт совпадают (до 1 % или 1 ₽ — округления, комиссии банка)."""
    return abs(abs(a) - abs(b)) <= max(Decimal(1), abs(a) / 100)


# ---------- предпросмотр и загрузка ----------


@dataclass
class AccountPlan:
    number: str
    account: MoneyAccount | None  # существующий счёт; None — создадим новый «из банка»
    name: str
    opening: Decimal | None
    closing: Decimal | None
    date_from: date | None
    date_to: date | None
    bik: str | None = None
    bank_name: str | None = None


@dataclass
class TxPlan:
    doc: Document
    key: str
    status: str  # new | duplicate
    kind: str = "regular"  # regular | ozon | transfer
    payer: dict | None = None  # маркетплейс по ИНН
    ozon_op: MoneyOperation | None = None  # выплата Ozon, которую подтверждает поступление
    manual: list[MoneyOperation] = field(default_factory=list)  # похожие ручные операции


@dataclass
class ImportResult:
    statement: Statement
    accounts: list[AccountPlan]
    rows: list[TxPlan]
    applied: bool
    already_loaded: datetime | None = None  # этот же файл загружали раньше
    candidates: list[MoneyAccount] = field(default_factory=list)  # счета без номера — к ним можно привязать выписку
    suggested_account_id: uuid.UUID | None = None
    warnings: list[str] = field(default_factory=list)

    def count(self, status: str | None = None, kind: str | None = None) -> int:
        return sum((status is None or r.status == status) and (kind is None or r.kind == kind) for r in self.rows)


def _account_name(number: str, bank_name: str | None) -> str:
    bank = (bank_name or "").replace('"', "").replace("«", "").replace("»", "")
    bank = bank.replace("ООО ", "").replace("АО ", "").replace("ПАО ", "").strip()
    return f"{bank or 'Расчётный счёт'} ···{number[-4:]}"[:100]


async def _plan_accounts(
    db: AsyncSession, user_id: uuid.UUID, st: Statement, account_id: uuid.UUID | None
) -> tuple[list[AccountPlan], list[MoneyAccount], uuid.UUID | None]:
    accounts = await planning_svc._accounts(db, user_id)
    by_number = {a.number: a for a in accounts if a.number}
    candidates = [a for a in accounts if a.archived_at is None and not a.number]
    chosen = None
    if account_id is not None:
        if len(st.accounts) != 1:
            raise BankImportError("В выписке несколько счетов — они найдутся по номеру; выбирать счёт не нужно")
        chosen = await planning_svc._account(db, user_id, account_id)
        if chosen.number and chosen.number != st.accounts[0].number:
            raise BankImportError(f"У счёта «{chosen.name}» другой номер — выберите другой счёт или «Новый счёт»")
    plans = []
    for sec in st.accounts:
        doc = next((d for d in st.documents if d.account == sec.number and (d.own_bik or d.own_bank)), None)
        acc = by_number.get(sec.number) or chosen
        plans.append(AccountPlan(
            sec.number, acc, acc.name if acc else _account_name(sec.number, doc.own_bank if doc else None),
            sec.opening, sec.closing, sec.date_from, sec.date_to,
            doc.own_bik if doc else None, doc.own_bank if doc else None,
        ))
    suggested = None
    if len(st.accounts) == 1 and plans[0].account is None and len(candidates) == 1:
        suggested = candidates[0].id  # один счёт без номера — скорее всего, он
    return plans, candidates, suggested


async def _ozon_ops(db: AsyncSession, user_id: uuid.UUID) -> list[MoneyOperation]:
    """Выплаты Ozon по данным Ozon, ещё не подтверждённые выпиской."""
    res = await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.source == "ozon_payout",
        MoneyOperation.external_key.like(f"{OZON_KEY}%"),
    )))
    return list(res.scalars())


async def _manual_ops(db: AsyncSession, user_id: uuid.UUID, lo: date, hi: date) -> list[MoneyOperation]:
    res = await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.source.in_(("manual", "import")),
        MoneyOperation.transfer_id.is_(None), MoneyOperation.day >= lo, MoneyOperation.day <= hi,
    )))
    return list(res.scalars())


def manual_fits(op: MoneyOperation, day: date, amount: Decimal, counterparty: str | None) -> bool:
    if Decimal(op.amount) != amount or abs((op.day - day).days) > MATCH_DAYS:
        return False
    return not (op.counterparty and counterparty) or _same_text(op.counterparty, counterparty)


async def analyze(
    db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, account_id: uuid.UUID | None = None
) -> ImportResult:
    try:
        st = parse(content)
    except StatementError as exc:
        raise BankImportError(str(exc)) from exc
    plans, candidates, suggested = await _plan_accounts(db, user_id, st, account_id)
    existing_ids = [p.account.id for p in plans if p.account]
    known: set[tuple[uuid.UUID, str]] = set()
    if existing_ids:
        res = await db.execute(select(BankTransaction.account_id, BankTransaction.dedup_key).where(
            BankTransaction.user_id == user_id, BankTransaction.account_id.in_(existing_ids)))
        known = {(a, k) for a, k in res.all()}
    own_numbers = {a.number for a in await planning_svc._accounts(db, user_id) if a.number} | set(st.numbers)
    account_of = {p.number: p.account for p in plans}
    days = [d.day for d in st.documents]
    manual = await _manual_ops(db, user_id, min(days) - timedelta(days=MATCH_DAYS), max(days) + timedelta(days=MATCH_DAYS)) \
        if days else []
    ozon_ops = await _ozon_ops(db, user_id)
    used_ozon: set[uuid.UUID] = set()
    used_manual: set[uuid.UUID] = set()
    seen: set[tuple[str, str]] = set()
    rows: list[TxPlan] = []
    for doc in st.documents:
        key = dedup_key(doc)
        acc = account_of.get(doc.account)
        duplicate = (doc.account, key) in seen or (acc is not None and (acc.id, key) in known)
        seen.add((doc.account, key))
        row = TxPlan(doc, key, "duplicate" if duplicate else "new")
        rows.append(row)
        if duplicate:
            continue
        payer = payers().get(doc.counterparty_inn or "")
        if doc.counterparty_account and doc.counterparty_account in own_numbers:
            row.kind = "transfer"
        elif payer and doc.amount > 0:
            row.payer = payer
            if payer["code"] == "ozon":
                row.kind = "ozon"
                row.ozon_op = next((
                    op for op in sorted(ozon_ops, key=lambda o: abs((doc.day - o.day).days))
                    if op.op_id not in used_ozon and 0 <= (doc.day - op.day).days <= OZON_DAYS
                    and ozon_close(Decimal(op.amount), doc.amount)
                ), None)
                if row.ozon_op is not None:
                    used_ozon.add(row.ozon_op.op_id)
        if row.kind == "regular":
            row.manual = [op for op in manual if op.op_id not in used_manual
                          and manual_fits(op, doc.day, doc.amount, doc.counterparty)]
            if row.manual:
                used_manual.add(row.manual[0].op_id)
    sha = hashlib.sha256(content).hexdigest()
    prev = (await db.execute(select(BankStatement).where(
        BankStatement.user_id == user_id, BankStatement.sha256 == sha).limit(1))).scalar_one_or_none()
    result = ImportResult(st, plans, rows, applied=False, already_loaded=prev.uploaded_at if prev else None,
                          candidates=candidates, suggested_account_id=suggested)
    result.warnings = list(st.warnings)
    return result


async def _counterpart_leg(db: AsyncSession, user_id: uuid.UUID, doc: Document) -> MoneyOperation | None:
    """Вторая часть перевода — операция банка на нашем счёте контрагента с обратной суммой, ещё без пары. Выписку по
    тому счёту загрузили раньше, когда этот счёт ещё не был известен, — там это обычная операция: делаем её частью
    перевода (новая версия, прежняя — в истории)."""
    acc = (await db.execute(select(MoneyAccount).where(
        MoneyAccount.user_id == user_id, MoneyAccount.number == doc.counterparty_account))).scalar_one_or_none()
    if acc is None:
        return None
    lo, hi = doc.day - timedelta(days=MATCH_DAYS), doc.day + timedelta(days=MATCH_DAYS)
    res = await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.account_id == acc.id, MoneyOperation.source == "bank",
        MoneyOperation.amount == -doc.amount, MoneyOperation.day >= lo, MoneyOperation.day <= hi,
    )))
    for leg in res.scalars():
        if leg.transfer_id is not None:
            if len(await planning_svc._transfer_legs(db, user_id, leg.transfer_id)) == 1:
                return leg
            continue
        tx = (await db.execute(select(BankTransaction).where(
            BankTransaction.user_id == user_id, BankTransaction.op_id == leg.op_id))).scalar_one_or_none()
        if tx is not None and tx.counterparty_account == doc.account:
            version = planning_svc._new_version(
                leg, transfer_id=uuid.uuid4(), category_id=await planning_svc._transfer_category(db, user_id))
            db.add(version)
            await db.flush()
            return version
    return None


async def apply(db: AsyncSession, user_id: uuid.UUID, result: ImportResult, content: bytes, filename: str) -> None:
    """Записать: выписку, счета, остатки, операции банка и операции планирования. Коммит — здесь."""
    st = result.statement
    sha = hashlib.sha256(content).hexdigest()
    statement = (await db.execute(select(BankStatement).where(
        BankStatement.user_id == user_id, BankStatement.sha256 == sha).limit(1))).scalar_one_or_none()
    if statement is None:
        statement = BankStatement(
            id=uuid.uuid4(), user_id=user_id, filename=filename[:255] or "выписка.txt", format="1c",
            version=st.version, encoding=st.encoding, sha256=sha, date_from=st.date_from, date_to=st.date_to,
            accounts=st.numbers, content=content,
            stats={"new": result.count("new"), "duplicate": result.count("duplicate"), "ozon": result.count("new", "ozon"),
                   "transfer": result.count("new", "transfer"), "skipped": len(st.skipped)},
        )
        db.add(statement)

    openings = await planning_svc._openings(db, user_id)
    accounts: dict[str, MoneyAccount] = {}
    for plan in result.accounts:
        acc = plan.account
        if acc is None:
            acc = MoneyAccount(id=uuid.uuid4(), user_id=user_id, name=plan.name, kind="checking", source="bank",
                               number=plan.number, bik=plan.bik, bank_name=plan.bank_name)
            db.add(acc)
        else:
            acc.number, acc.source = plan.number, "bank"
            acc.bik = acc.bik or plan.bik
            acc.bank_name = acc.bank_name or plan.bank_name
        accounts[plan.number] = acc
        # начальный остаток — из выписки, если его нет или выписка начинается раньше и доходит до него (банк — факт);
        # иначе (между выпиской и остатком пробел) — оставляем остаток пользователя, расхождение — в сверке ниже
        current = openings.get(acc.id)
        if plan.opening is not None and plan.date_from and (
            current is None
            or (plan.date_from < current.as_of and plan.date_to and plan.date_to >= current.as_of - timedelta(days=1))
        ):
            db.add(MoneyAccountBalance(user_id=user_id, account_id=acc.id, amount=plan.opening, as_of=plan.date_from,
                                       created_at=_now()))
            if current is not None:
                result.warnings.append(
                    f"«{acc.name}»: начальный остаток — из выписки, {plan.opening} ₽ на начало {plan.date_from:%d.%m.%Y} "
                    f"(прежний — {Decimal(current.amount)} ₽ на {current.as_of:%d.%m.%Y} — остался в истории)."
                )
        for kind, day, amount in (("opening", plan.date_from, plan.opening), ("closing", plan.date_to, plan.closing)):
            if day is None or amount is None:
                continue
            same = (await db.execute(select(BankBalance).where(
                BankBalance.account_id == acc.id, BankBalance.day == day, BankBalance.kind == kind,
            ).order_by(BankBalance.created_at.desc()).limit(1))).scalar_one_or_none()
            if same is None or Decimal(same.amount) != amount:
                db.add(BankBalance(user_id=user_id, account_id=acc.id, statement_id=statement.id, day=day, kind=kind,
                                   amount=amount))
    await db.flush()

    cats = {c.code: c.id for c in await planning_svc._categories(db, user_id) if c.code and c.archived_at is None}
    if "marketplace" not in cats and any(r.payer for r in result.rows):
        await planning_svc.add_template_categories(db, user_id, ["marketplace"])
        await db.flush()
        cats = {c.code: c.id for c in await planning_svc._categories(db, user_id) if c.code and c.archived_at is None}
    for row in result.rows:
        if row.status != "new":
            continue
        doc = row.doc
        acc = accounts[doc.account]
        tx = BankTransaction(
            id=uuid.uuid4(), user_id=user_id, account_id=acc.id, statement_id=statement.id, day=doc.day,
            amount=doc.amount, counterparty=(doc.counterparty or "")[:300] or None, counterparty_inn=doc.counterparty_inn,
            counterparty_account=doc.counterparty_account, counterparty_bik=doc.counterparty_bik, purpose=doc.purpose,
            doc_number=doc.number, dedup_key=row.key, source="file_1c", kind=row.kind,
            raw_json={k: v for k, v in doc.raw.items()},
        )
        db.add(tx)
        if row.kind == "ozon" and row.ozon_op is not None:
            # одна операция: выплата по данным Ozon подтверждена выпиской — дата, сумма и счёт теперь из банка
            op = planning_svc._new_version(row.ozon_op, source="bank", day=doc.day, amount=doc.amount, account_id=acc.id)
            db.add(op)
        elif row.kind == "transfer":
            pair = await _counterpart_leg(db, user_id, doc)
            op = MoneyOperation(
                op_id=uuid.uuid4(), version=1, user_id=user_id, account_id=acc.id, day=doc.day, amount=doc.amount,
                category_id=await planning_svc._transfer_category(db, user_id), counterparty=doc.counterparty,
                comment=doc.purpose, source="bank", transfer_id=pair.transfer_id if pair else uuid.uuid4(),
            )
            db.add(op)
        else:
            op = MoneyOperation(
                op_id=uuid.uuid4(), version=1, user_id=user_id, account_id=acc.id, day=doc.day, amount=doc.amount,
                category_id=cats.get("marketplace") if row.payer else None,
                counterparty=row.payer["name"] if row.payer else doc.counterparty, comment=doc.purpose, source="bank",
                external_key=f"{OZON_BANK_KEY}{doc.day.isoformat()}:{tx.id.hex[:8]}" if row.kind == "ozon" else None,
            )
            db.add(op)
        tx.op_id = op.op_id
        await db.flush()
    await db.commit()
    result.applied = True
    # остаток по выписке и по операциям сервиса на конец периода — сверка
    for plan in result.accounts:
        acc = accounts[plan.number]
        if plan.closing is None or plan.date_to is None:
            continue
        computed = (await planning_svc.balances(db, user_id, plan.date_to)).get(acc.id)
        if computed is not None and computed != plan.closing:
            result.warnings.append(
                f"«{acc.name}»: на {plan.date_to:%d.%m.%Y} по выписке {plan.closing} ₽, по операциям сервиса {computed} ₽ — "
                "проверьте начальный остаток счёта и возможные дубли с ручными операциями."
            )


# ---------- возможные дубли с ручными операциями ----------


async def merge_suggestions(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[MoneyOperation, MoneyOperation]]:
    """Пары (операция банка, похожая ручная): сумма, дата ± 3 дня, контрагент. «Это разные» — не предлагаем."""
    bank = list((await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.source == "bank", MoneyOperation.transfer_id.is_(None),
    )).order_by(MoneyOperation.day))).scalars())
    if not bank:
        return []
    manual = await _manual_ops(db, user_id, bank[0].day - timedelta(days=MATCH_DAYS), bank[-1].day + timedelta(days=MATCH_DAYS))
    dismissed = {(d.bank_op_id, d.manual_op_id) for d in (await db.execute(select(MergeDismissal).where(
        MergeDismissal.user_id == user_id))).scalars()}
    used: set[uuid.UUID] = set()
    out = []
    for b in bank:
        for m in sorted(manual, key=lambda op: abs((op.day - b.day).days)):
            if m.op_id in used or (b.op_id, m.op_id) in dismissed:
                continue
            if manual_fits(m, b.day, Decimal(b.amount), b.counterparty):
                out.append((b, m))
                used.add(m.op_id)
                break
    return out


async def merge(db: AsyncSession, user_id: uuid.UUID, bank_op_id: uuid.UUID, manual_op_id: uuid.UUID) -> MoneyOperation:
    """Объединить: ручная операция скрывается (в истории остаётся), её статья и описание переходят к операции банка."""
    bank = await planning_svc.current_operation(db, user_id, bank_op_id)
    manual = await planning_svc.current_operation(db, user_id, manual_op_id)
    if bank.source != "bank" or manual.source not in ("manual", "import") or manual.transfer_id is not None:
        raise PlanningError("Объединить можно операцию из банка с операцией, внесённой вручную или файлом")
    changes = {}
    if bank.category_id is None and manual.category_id is not None:
        changes["category_id"] = manual.category_id
    if manual.comment:
        changes["comment"] = manual.comment if not bank.comment else f"{manual.comment}\n{bank.comment}"[:2000]
    manual.deleted_at = _now()
    if changes:
        version = planning_svc._new_version(bank, **changes)
        db.add(version)
        return version
    return bank


async def dismiss(db: AsyncSession, user_id: uuid.UUID, bank_op_id: uuid.UUID, manual_op_id: uuid.UUID) -> None:
    await planning_svc.current_operation(db, user_id, bank_op_id)
    await planning_svc.current_operation(db, user_id, manual_op_id)
    db.add(MergeDismissal(user_id=user_id, bank_op_id=bank_op_id, manual_op_id=manual_op_id))
