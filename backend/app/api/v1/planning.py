"""Планирование (шаг 3.8) — необязательный раздел: счета и остатки, статьи, операции. Работает без банка."""
import uuid
from datetime import date, timedelta

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.models.planning import CashCategory, MoneyAccount
from app.models.user import User
from app.schemas.planning import (
    Account,
    AccountIn,
    AccountHistory,
    AccountPatch,
    BulkIn,
    BulkOut,
    Category,
    CategoryIn,
    CategoryPatch,
    ImportCounts,
    ImportResponse,
    ImportRowOut,
    JournalPage,
    OperationIn,
    OperationPatch,
    OperationSaved,
    OperationsPage,
    OperationVersion,
    PlanningSettingsIn,
    PlanningState,
    ReconcileOut,
    RuleSuggestion,
    SetupIn,
    SetupOptions,
    TemplateCodes,
    UnrecognizedPage,
)
from app.services import accounts_view, audit, journal, operation_import
from app.services import planning as svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk
from app.services.templates import MEDIA_TYPES

MAX_FILE_MB = 10

router = APIRouter(prefix="/planning", tags=["planning"])


def _fail(exc: svc.PlanningError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if isinstance(exc, svc.NotFound) else status.HTTP_400_BAD_REQUEST
    return HTTPException(code, str(exc))


# ---------- раздел и мастер ----------


@router.get("/settings", response_model=PlanningState)
async def get_state(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return await svc.state(db, user.id)


@router.put("/settings", response_model=PlanningState)
async def put_settings(data: PlanningSettingsIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Включить или выключить раздел (данные остаются), куда приходят выплаты Ozon, порог остатка, горизонт."""
    try:
        await svc.update_settings(db, user.id, data.model_dump(exclude_unset=True))
    except svc.PlanningError as exc:
        raise _fail(exc) from exc
    return await svc.state(db, user.id)


@router.get("/setup", response_model=SetupOptions)
async def setup_options(user: User = Depends(get_current_store)):
    """Статьи шаблона с пояснением «зачем» и какие группы предложить по ответу «Откуда товар»."""
    groups = svc._template()["groups_by_supplier"]
    return SetupOptions(categories=svc.template_categories(), groups_by_supplier=groups)


@router.post("/setup", response_model=PlanningState)
async def post_setup(data: SetupIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Мастер первого входа: любой вопрос можно пропустить — тогда ничего не предлагаем и считаем как есть."""
    if (await svc.state(db, user.id))["setup_done"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Раздел уже настроен — счета и статьи меняются на его страницах")
    if data.quick:  # «Пропустить настройку»: один счёт, сюда же выплаты Ozon; статьи «Приход» и «Расход»
        setup = svc.SetupIn(ozon_account=0, categories=["income", "expense"])
    else:
        setup = svc.SetupIn(
            supplier_payment=data.supplier_payment, supplier_deferral_days=data.supplier_deferral_days,
            accounts=[svc.SetupAccount(a.name, a.kind, a.balance) for a in data.accounts],
            ozon_account=data.ozon_account, categories=data.categories,
        )
    try:
        await svc.setup(db, user.id, setup)
    except svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return await svc.state(db, user.id)


# ---------- счета ----------


@router.get("/accounts", response_model=list[Account])
async def list_accounts(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Счета с остатком на сегодня: начальный остаток + операции с его даты. Нет остатка — null, а не 0."""
    await svc.sync_ozon_operations(db, user.id)
    return await svc.accounts(db, user.id)


async def _account_out(db: AsyncSession, user: User, account_id: uuid.UUID) -> dict:
    return next(a for a in await svc.accounts(db, user.id) if a["id"] == account_id)


@router.post("/accounts", response_model=Account, status_code=status.HTTP_201_CREATED)
async def create_account(data: AccountIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        acc = await svc.create_account(db, user.id, data.name, data.kind, data.balance, data.as_of)
    except svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return await _account_out(db, user, acc.id)


@router.patch("/accounts/{account_id}", response_model=Account)
async def patch_account(
    account_id: uuid.UUID, data: AccountPatch, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Название и тип; новый начальный остаток — новая запись (прежний в истории); archived — в архив и обратно."""
    try:
        await svc.update_account(db, user.id, account_id, data.model_dump(exclude_unset=True))
    except svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return await _account_out(db, user, account_id)


# ---------- статьи ----------


@router.get("/accounts/reconcile", response_model=list[ReconcileOut])
async def reconcile_accounts(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Сверка расчётного остатка каждого счёта с выпиской банка: сходится ли, на сколько и с какого дня разошлось,
    какие операции сервиса в этот день без пары в банке."""
    await svc.sync_ozon_operations(db, user.id)
    return await accounts_view.reconcile(db, user.id)


@router.get("/accounts/{account_id}/history", response_model=AccountHistory)
async def account_history(
    account_id: uuid.UUID,
    date_from: date | None = Query(None, description="По умолчанию — 30 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    type_: Literal["income", "expense", "transfer"] | None = Query(None, alias="type"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Поступления, списания и переводы по счёту с остатком после каждой операции."""
    date_to = date_to or today_msk()
    date_from = date_from or date_to - timedelta(days=29)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days > 3660:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: не больше 10 лет за раз")
    await svc.sync_ozon_operations(db, user.id)
    try:
        return await accounts_view.history(db, user.id, account_id, date_from, date_to, type_)
    except svc.PlanningError as exc:
        raise _fail(exc) from exc


@router.get("/unrecognized", response_model=UnrecognizedPage)
async def unrecognized_operations(
    account_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=500),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Нераспознанные: операции из банка и из файла без статьи — с назначением, ИНН и заготовкой правила."""
    return await accounts_view.unrecognized(db, user.id, account_id, limit)


@router.get("/categories", response_model=list[Category])
async def list_categories(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return await svc.categories(db, user.id)


@router.post("/categories", response_model=Category, status_code=status.HTTP_201_CREATED)
async def create_category(data: CategoryIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        cat = await svc.create_category(db, user.id, data.name, data.kind, data.parent_id, data.in_pnl, data.in_budget)
    except svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return next(c for c in await svc.categories(db, user.id) if c["id"] == cat.id)


@router.post("/categories/from-template", response_model=list[Category])
async def add_from_template(data: TemplateCodes, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Добавить статьи из шаблона (по кодам) — тех, которых ещё нет; статья из архива возвращается."""
    known = {c["code"] for c in svc.template_categories()}
    if unknown := [c for c in data.codes if c not in known]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Статья не из шаблона: {', '.join(unknown)}")
    await svc.add_template_categories(db, user.id, data.codes)
    await db.commit()
    return await svc.categories(db, user.id)


@router.patch("/categories/{category_id}", response_model=Category)
async def patch_category(
    category_id: uuid.UUID, data: CategoryPatch, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    try:
        extra = {"parent_id": data.parent_id} if "parent_id" in data.model_fields_set else {}
        cat = await svc.update_category(db, user.id, category_id, data.name, data.archived, in_pnl=data.in_pnl,
                                        in_budget=data.in_budget, **extra)
    except svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return next(c for c in await svc.categories(db, user.id) if c["id"] == cat.id)


# ---------- операции ----------


@router.get("/operations", response_model=OperationsPage)
async def list_operations(
    date_from: date | None = Query(None, description="По умолчанию — 30 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    search: str | None = Query(None, max_length=100, description="Контрагент или описание"),
    uncategorized: bool = Query(False, description="Только без статьи (переводы не в счёт)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Операции периода, новые сверху; перевод — одной строкой. Выплаты Ozon — уже здесь, вводить их не нужно."""
    date_to = date_to or today_msk()
    date_from = date_from or date_to - timedelta(days=29)
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (date_to - date_from).days > 3660:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: не больше 10 лет за раз")
    await svc.sync_ozon_operations(db, user.id)
    return await svc.list_operations(db, user.id, date_from, date_to, account_id, category_id, search, limit, offset,
                                     uncategorized)


class _JournalQuery:
    def __init__(
        self,
        date_from: date | None = Query(None, description="По умолчанию — 30 дней назад"),
        date_to: date | None = Query(None, description="По умолчанию — сегодня"),
        status_: Literal["fact", "planned", "expected", "overdue"] | None = Query(None, alias="status",
                                                                        description="Факт, план или просроченные"),
        type_: Literal["income", "expense", "transfer"] | None = Query(None, alias="type"),
        account_id: uuid.UUID | None = None,
        category_id: uuid.UUID | None = Query(None, description="Статья — вместе с подстатьями"),
        uncategorized: bool = Query(False, description="Только без статьи (переводы не в счёт)"),
        source: Literal["manual", "import", "ozon_payout", "bank", "plan", "supplier", "tax", "ozon_forecast"] | None = None,
        search: str | None = Query(None, max_length=100, description="Контрагент, описание, название платежа"),
        sort: Literal["day", "amount", "category"] = "day",
        order: Literal["asc", "desc"] = "desc",
        rule_id: uuid.UUID | None = Query(None, description="Операции, которым статью поставило это правило"),
    ):
        date_to = date_to or today_msk()
        date_from = date_from or date_to - timedelta(days=29)
        if date_from > date_to:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
        if (date_to - date_from).days > 3660:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: не больше 10 лет за раз")
        self.date_from, self.date_to = date_from, date_to
        self.kw = dict(status=status_, type_=type_, account_id=account_id, category_id=category_id,
                       uncategorized=uncategorized, source=source, search=search, sort=sort, desc=order == "desc",
                       rule_id=rule_id)


@router.get("/journal", response_model=JournalPage)
async def operations_journal(
    q: _JournalQuery = Depends(),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """«Все операции»: факт (операции) и план (неоплаченные платежи календаря) одним списком со статусом и источником."""
    await svc.sync_ozon_operations(db, user.id)
    return await journal.journal(db, user.id, q.date_from, q.date_to, limit=limit, offset=offset, **q.kw)


@router.get("/journal/export", response_class=Response)
async def operations_journal_export(q: _JournalQuery = Depends(), user: User = Depends(get_current_store),
                                    db: AsyncSession = Depends(get_db)):
    """Тот же список в Excel — все строки по фильтрам."""
    await svc.sync_ozon_operations(db, user.id)
    data = await journal.journal(db, user.id, q.date_from, q.date_to, limit=10 ** 9, **q.kw)
    return attachment(
        journal.to_xlsx(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"Операции {q.date_from:%d.%m.%Y}-{q.date_to:%d.%m.%Y}.xlsx", "operations.xlsx",
    )


@router.post("/operations/bulk", response_model=BulkOut)
async def bulk_operations(data: BulkIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Массовое изменение выбранных операций: статья, счёт, контрагент, описание. Каждой — новая версия; не подходит
    хоть одна — ничего не меняется, в ответе — какая и почему."""
    changes = {k: getattr(data, k) for k in ("category_id", "account_id", "counterparty", "comment")
               if k in data.model_fields_set}
    if "account_id" in changes and changes["account_id"] is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Выберите счёт")
    try:
        changed, unchanged = await journal.bulk_update(db, user.id, data.op_ids, changes)
    except svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return BulkOut(changed=changed, unchanged=unchanged)


@router.post("/operations", response_model=OperationSaved, status_code=status.HTTP_201_CREATED)
async def create_operation(data: OperationIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Приход, расход или перевод между своими счетами (две операции: со счёта и на счёт). Статью можно вписать
    названием (category_name): такая уже есть — берём её, нет — сохраняем новую в список статей."""
    note = None
    try:
        if data.type != "transfer" and data.category_id is None and data.category_name:
            category, note = await svc.resolve_category(db, user.id, data.category_name, data.type)
            data.category_id = category.id
        if data.type == "transfer":
            if data.to_account_id is None:
                raise svc.PlanningError("Перевод: выберите, на какой счёт")
            op, _ = await svc.create_transfer(db, user.id, data.account_id, data.to_account_id, data.amount, data.day,
                                              data.comment)
        else:
            amount = data.amount if data.type == "income" else -data.amount
            op = await svc.create_operation(db, user.id, svc.OperationIn(
                data.day, amount, data.account_id, data.category_id, data.counterparty, data.comment,
            ))
    except svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    return await _saved(db, user, op, True, note)


async def _saved(db: AsyncSession, user: User, op, changed: bool, note: str | None) -> OperationSaved:
    cat = await db.get(CashCategory, op.category_id) if op.category_id else None
    return OperationSaved(
        op_id=op.op_id, version=op.version, changed=changed,
        category={"id": cat.id, "name": cat.name} if cat and cat.user_id == user.id else None, category_note=note,
    )


@router.patch("/operations/{op_id}", response_model=OperationSaved)
async def patch_operation(
    op_id: uuid.UUID, data: OperationPatch, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Исправление — новая версия операции, прежняя остаётся в истории (правило 12)."""
    changes = data.model_dump(exclude_unset=True)
    kind = changes.pop("type", None)
    category_name = changes.pop("category_name", None)
    note = None
    try:
        row = await svc.current_operation(db, user.id, op_id)
        if category_name and changes.get("category_id") is None and row.transfer_id is None:
            negative = (kind == "expense") if kind is not None else row.amount < 0
            category, note = await svc.resolve_category(db, user.id, category_name, "expense" if negative else "income")
            changes["category_id"] = category.id
        if "amount" in changes or kind is not None:
            value = changes.get("amount", abs(row.amount))
            negative = (kind == "expense") if kind is not None else row.amount < 0
            changes["amount"] = -value if negative and row.transfer_id is None else value
        saved, changed = await svc.update_operation(db, user.id, op_id, changes)
    except svc.PlanningError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    await db.commit()
    result = await _saved(db, user, saved, changed or note in ("created", "restored"), note)
    if changed and "category_id" in changes:  # статью поставили вручную — предложить правило для контрагента (3.12)
        from app.services import categorization

        suggestion = await categorization.suggestion(db, user.id, saved)
        result.rule_suggestion = RuleSuggestion(**suggestion) if suggestion else None
    return result


@router.delete("/operations/{op_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operation(op_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Скрыть операцию (у перевода — обе части); в истории она остаётся."""
    try:
        await svc.delete_operation(db, user.id, op_id)
    except svc.PlanningError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/operations/{op_id}/history", response_model=list[OperationVersion])
async def operation_history(op_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        rows = await svc.history(db, user.id, op_id)
    except svc.PlanningError as exc:
        raise _fail(exc) from exc
    accounts = {a.id: a.name for a in await _all(db, MoneyAccount, user)}
    cats = {c.id: c.name for c in await _all(db, CashCategory, user)}
    return [
        OperationVersion(
            version=r.version, day=r.day, amount=r.amount, account=accounts.get(r.account_id, "—"),
            category=cats.get(r.category_id), counterparty=r.counterparty, comment=r.comment, source=r.source,
            created_at=r.created_at, superseded_at=r.superseded_at, deleted_at=r.deleted_at,
        )
        for r in rows
    ]


async def _all(db: AsyncSession, model, user: User):
    return (await db.execute(select(model).where(model.user_id == user.id))).scalars().all()


# ---------- операции списком: шаблон и загрузка ----------


def _file(content: bytes, fmt: str, name: str, fallback: str) -> Response:
    return attachment(content, MEDIA_TYPES[fmt], f"{name}.{fmt}", f"{fallback}.{fmt}")


@router.get("/operations/template", response_class=Response)
async def operations_template(
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    rows: Literal["all", "empty"] = Query("all", description="all — с операциями периода, empty — пустой"),
    date_from: date | None = None,
    date_to: date | None = None,
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Шаблон операций со своими счетами и статьями (списком в Excel) и операциями периода: скачал → заполнил →
    загрузил в /operations/import."""
    content = await operation_import.template(db, user.id, fmt, date_from, date_to, rows == "empty")
    name = "Операции — шаблон" if rows == "empty" else f"Операции {today_msk():%d.%m.%Y}"
    return _file(content, fmt, name, "operations")


async def _read_upload(file: UploadFile) -> bytes:
    limit = MAX_FILE_MB * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Файл больше {MAX_FILE_MB} МБ")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    return content


@router.post("/operations/import", response_model=ImportResponse)
async def import_operations(
    request: Request,
    file: UploadFile = File(..., description="XLSX или CSV по шаблону: дата, приход / расход, счёт, статья"),
    dry_run: bool = Query(True, description="true — только предпросмотр, false — применить"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    if dry_run:
        audit.not_a_change(request)
    content = await _read_upload(file)
    try:
        result = await operation_import.import_operations(
            db, user.id, content, file.filename or "", apply=not dry_run, today=today_msk()
        )
    except (operation_import.OperationImportError, svc.PlanningError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ImportResponse(
        applied=result.applied,
        counts=ImportCounts(**{s: result.count(s) for s in ("new", "changed", "unchanged", "skipped", "error")}),
        rows=[ImportRowOut.model_validate(r, from_attributes=True) for r in result.rows],
    )


@router.post("/operations/import/errors", response_class=Response, dependencies=[Depends(audit.read_only)])
async def import_errors_file(
    file: UploadFile = File(..., description="Тот же файл, что загружали в /operations/import"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: исправить и загрузить снова."""
    content = await _read_upload(file)
    try:
        out, fmt = await operation_import.error_file(db, user.id, content, file.filename or "", today_msk())
    except operation_import.OperationImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _file(out, fmt, "Операции — строки с ошибками", "operations-errors")

