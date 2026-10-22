"""Банк (шаг 3.11) — необязательный источник факта для «Планирования»: выписка файлом в формате 1С (любой банк),
счета с реквизитами, операции банка, возможные дубли с ручными операциями, сверка выплат Ozon с поступлениями."""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.planning import _fail, _read_upload
from app.db.session import get_db
from app.models.user import User
from app.schemas.bank import (
    BankAccount,
    BankTxPage,
    MergeIn,
    MergeSuggestion,
    OzonCheck,
    StatementCounts,
    StatementInfo,
    StatementPreview,
)
from app.services import audit
from app.services import bank as svc
from app.services import bank_import
from app.services import planning as planning_svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/bank", tags=["bank"])


def _masked(number: str) -> str:
    return f"···{number[-4:]}"


def _preview(result: bank_import.ImportResult) -> StatementPreview:
    st = result.statement
    rows = result.rows
    return StatementPreview(
        applied=result.applied, version=st.version, encoding=st.encoding, date_from=st.date_from, date_to=st.date_to,
        accounts=[
            {"number": p.number, "masked": _masked(p.number), "name": p.name, "date_from": p.date_from,
             "date_to": p.date_to, "opening": p.opening, "closing": p.closing,
             "account": {"id": p.account.id, "name": p.account.name} if p.account else None}
            for p in result.accounts
        ],
        counts=StatementCounts(
            new=result.count("new"), duplicate=result.count("duplicate"), ozon=result.count("new", "ozon"),
            ozon_linked=sum(r.status == "new" and r.ozon_op is not None for r in rows),
            transfer=result.count("new", "transfer"), manual=sum(r.status == "new" and bool(r.manual) for r in rows),
            skipped=len(st.skipped),
        ),
        rows=[
            {"day": r.doc.day, "amount": r.doc.amount, "account": r.doc.account, "counterparty": r.doc.counterparty,
             "counterparty_inn": r.doc.counterparty_inn, "purpose": r.doc.purpose, "doc_number": r.doc.number,
             "status": r.status, "kind": r.kind, "payer": r.payer["name"] if r.payer else None,
             "ozon_payout": r.ozon_op.day if r.ozon_op is not None else None, "manual": [m.op_id for m in r.manual]}
            for r in rows
        ],
        skipped=st.skipped, warnings=result.warnings, already_loaded=result.already_loaded,
        candidates=[{"id": a.id, "name": a.name} for a in result.candidates],
        suggested_account_id=result.suggested_account_id,
    )


@router.post("/statements/upload", response_model=StatementPreview)
async def upload_statement(
    request: Request,
    file: UploadFile = File(..., description="Выписка в формате 1С (1CClientBankExchange, обычно .txt)"),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — загрузить"),
    account_id: uuid.UUID | None = Query(None, description="К какому счёту без номера привязать выписку; пусто — по номеру или новый"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Предпросмотр: счёт, период, сколько новых операций, уже загруженных, поступлений от Ozon и похожих на ручные.
    dry_run=false — записать. Повторная загрузка того же периода дублей не создаёт."""
    if dry_run:
        audit.not_a_change(request)
    content = await _read_upload(file)
    try:
        result = await bank_import.analyze(db, user.id, content, file.filename or "", account_id)
        if not dry_run:
            await bank_import.apply(db, user.id, result, content, file.filename or "")
    except bank_import.BankImportError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    if not dry_run:
        await planning_svc.sync_ozon_operations(db, user.id)
    return _preview(result)


@router.get("/accounts", response_model=list[BankAccount])
async def bank_accounts(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Счета с реквизитами и остатком по последней выписке рядом с остатком по операциям сервиса."""
    return await svc.accounts(db, user.id)


@router.get("/transactions", response_model=BankTxPage)
async def bank_transactions(
    account_id: uuid.UUID | None = None,
    date_from: date | None = Query(None, description="По умолчанию — 90 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    date_to = date_to or today_msk()
    date_from = date_from or date_to - timedelta(days=89)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    return await svc.transactions(db, user.id, account_id, date_from, date_to, limit, offset)


@router.get("/statements", response_model=list[StatementInfo])
async def bank_statements(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Загруженные выписки — файлы хранятся (правило 12)."""
    return await svc.statements(db, user.id)


async def _brief(db: AsyncSession, user: User, ops) -> list[MergeSuggestion]:
    names = {a.id: a.name for a in await planning_svc._accounts(db, user.id)}
    cats = {c.id: c.name for c in await planning_svc._categories(db, user.id)}

    def brief(op):
        return {"op_id": op.op_id, "day": op.day, "amount": op.amount, "account": names.get(op.account_id, "—"),
                "counterparty": op.counterparty, "category": cats.get(op.category_id), "comment": op.comment}

    return [MergeSuggestion(bank=brief(b), manual=brief(m)) for b, m in ops]


@router.get("/matches", response_model=list[MergeSuggestion])
async def matches(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Возможные дубли: операция из выписки и похожая ручная (сумма, дата ± 3 дня, контрагент). Сами не объединяем."""
    return await _brief(db, user, await bank_import.merge_suggestions(db, user.id))


@router.post("/matches/merge", status_code=status.HTTP_204_NO_CONTENT)
async def merge(data: MergeIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Объединить: ручная операция скрывается (остаётся в истории), её статья и описание — у операции банка."""
    try:
        await bank_import.merge(db, user.id, data.bank_op_id, data.manual_op_id)
    except planning_svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/matches/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(data: MergeIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Это разные операции» — больше не предлагать."""
    try:
        await bank_import.dismiss(db, user.id, data.bank_op_id, data.manual_op_id)
    except planning_svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ozon-check", response_model=OzonCheck)
async def ozon_check(
    date_from: date | None = Query(None, description="По умолчанию — 60 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Сверка: выплата по данным Ozon ↔ поступление на счёт по выписке — суммы и даты, расхождения."""
    date_to = date_to or today_msk()
    date_from = date_from or date_to - timedelta(days=59)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    return await svc.ozon_check(db, user.id, date_from, date_to)
