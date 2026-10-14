"""Операции планирования списком: шаблон со своими операциями → заполнить → загрузить (шаг 3.8, правило 14).

На общем модуле `services/templates`: файл, скачанный из сервиса, загружается обратно без ошибок. Строка с ID — это
существующая операция: изменилась — новая версия (правило 12), не изменилась — «без изменений». Строка без ID — новая
операция; такая же уже есть (дата, сумма, счёт, статья, контрагент, комментарий) — пропускается, чтобы повторная
загрузка того же файла не задвоила операции. Переводы между своими счетами и выплаты Ozon в шаблон не входят: первые
добавляются на странице, вторые приходят сами.
"""
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.xlsx.common import cell_text
from app.models.planning import CashCategory, MoneyAccount, MoneyOperation
from app.services import planning as svc
from app.services.templates import (
    ERROR_COLUMN,
    CellError,
    Column,
    TemplateError,
    TemplateSpec,
    build_file,
    echo,
    is_empty,
    parse_day,
    parse_money,
    read_table,
)

MAX_ROWS = 20_000
Status = Literal["new", "changed", "unchanged", "skipped", "error"]
SKIPPED_EMPTY = "Сумма не указана — строка пропущена"
SKIPPED_DUPLICATE = (
    "Такая операция уже есть — не добавляем второй раз. Если это действительно ещё одна, добавьте её на странице"
)

SPEC = TemplateSpec(
    sheet="Операции",
    columns=(
        Column("op_id", "ID (не меняйте)", width=38, aliases=("id операции", "id"),
               hint="Пусто — новая операция. С ID — изменение этой операции: ID не меняйте и не копируйте в новые строки."),
        Column("day", "Дата", kind="date", width=13, aliases=("дата операции", "дата", "date"),
               hint="Когда пришли или ушли деньги, ДД.ММ.ГГГГ. Не позже сегодняшнего дня."),
        Column("income", "Приход, руб.", kind="money", width=15, aliases=("приход", "поступлени", "поступило"),
               hint="Сколько пришло — число больше 0. В строке заполните либо «Приход», либо «Расход»."),
        Column("expense", "Расход, руб.", kind="money", width=15, aliases=("расход", "списани", "списано"),
               hint="Сколько ушло — число больше 0, без минуса."),
        Column("amount", "Сумма, руб.", kind="money", in_template=False, aliases=("сумма", "amount"),
               exclude=("приход", "расход"), hint="Вместо «Приход» и «Расход»: + приход, − расход."),
        Column("account", "Счёт", width=22, aliases=("счёт", "счет", "кошел", "касса", "account"),
               hint="Как в разделе «Счета»: например, «Расчётный счёт» или «Наличные». Один счёт — можно не заполнять."),
        Column("category", "Статья", width=26, aliases=("статья", "категори", "category"),
               hint="Как в разделе «Статьи». Пусто — без статьи."),
        Column("counterparty", "Контрагент", width=24, aliases=("контрагент", "получатель", "плательщик"),
               hint="Необязательно: кому заплатили или от кого пришло."),
        Column("comment", "Комментарий", width=36, aliases=("комментарий", "назначение", "примечание", "описание"),
               hint="Необязательно."),
    ),
    required=(("day",), ("income", "expense", "amount")),
    missing_columns=(
        "Не найдены колонки «Дата» и «Приход» / «Расход». Скачайте шаблон на странице «Планирование → Операции» — "
        "или назовите колонки так же: дата, приход, расход (или сумма со знаком), счёт, статья, комментарий."
    ),
    instructions=(
        "1. Одна строка — одна операция: дата и сумма в колонке «Приход» или «Расход».",
        "2. Счёт и статья — как в сервисе (в Excel их можно выбрать из списка). Контрагент и комментарий — по желанию.",
        "3. Строки с ID — уже внесённые операции: исправьте их прямо здесь, ID не трогайте.",
        "4. Сохраните файл и загрузите на странице «Планирование → Операции». Сначала покажем, что изменится, — "
        "применится только после подтверждения.",
        "Переводы между своими счетами добавляйте на странице («Перевод»), выплаты Ozon вносить не нужно — они "
        "появляются сами.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и не удаляйте строку заголовков — по ним мы находим колонки.",
        "• Расход пишите без минуса в колонку «Расход». Если у вас одна колонка «Сумма» со знаком — тоже можно: "
        "+ приход, − расход.",
        "• Пример: 14.10.2026, расход 25 000, счёт «Расчётный счёт», статья «Зарплата», контрагент «Иванов», "
        "комментарий «аванс».",
    ),
)


class OperationImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


@dataclass
class ImportRow:
    line: int
    op_id: uuid.UUID | None = None
    day: date | None = None
    amount: Decimal | None = None
    account: str | None = None
    category: str | None = None
    counterparty: str | None = None
    comment: str | None = None
    status: Status = "new"
    message: str | None = None
    account_id: uuid.UUID | None = field(default=None, repr=False)
    category_id: uuid.UUID | None = field(default=None, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ImportResult:
    rows: list[ImportRow]
    applied: bool

    def count(self, status: Status) -> int:
        return sum(r.status == status for r in self.rows)


@dataclass
class Directory:
    accounts: list[MoneyAccount]
    categories: list[CashCategory]

    def active_accounts(self) -> list[MoneyAccount]:
        return [a for a in self.accounts if a.archived_at is None]


async def _directory(db: AsyncSession, user_id: uuid.UUID) -> Directory:
    accounts = (await db.execute(select(MoneyAccount).where(MoneyAccount.user_id == user_id)
                                 .order_by(MoneyAccount.created_at))).scalars().all()
    cats = (await db.execute(select(CashCategory).where(CashCategory.user_id == user_id)
                             .order_by(CashCategory.created_at))).scalars().all()
    return Directory(list(accounts), list(cats))


def _safe_choices(names: list[str]) -> tuple[str, ...]:
    """Выпадающий список Excel: без запятых и кавычек и не длиннее 255 знаков — иначе списка нет (ввести можно всё)."""
    names = [n for n in names if "," not in n and '"' not in n]
    return tuple(names) if names and len(",".join(names)) <= 250 else ()


def spec_for(directory: Directory) -> tuple[TemplateSpec, list[Column]]:
    """Шаблон пользователя: в колонках «Счёт» и «Статья» — его счета и статьи списком."""
    accounts = _safe_choices([a.name for a in directory.active_accounts()])
    cats = _safe_choices([c.name for c in directory.categories if c.archived_at is None and c.kind != "transfer"])
    columns = [
        replace(c, choices=accounts) if c.key == "account" else replace(c, choices=cats) if c.key == "category" else c
        for c in SPEC.template_columns
    ]
    return SPEC, columns


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _text(value: Any, limit: int) -> str | None:
    if not is_empty(value) and not isinstance(value, (str, int, float, Decimal, date)):
        raise CellError("формула без сохранённого значения — откройте файл в Excel и сохраните его")
    return cell_text(value)[:limit] or None


def _cell(raw: dict, key: str, parser, label: str, errors: list[str]) -> Any:
    if key not in raw:
        return None
    try:
        return parser(raw[key])
    except CellError as exc:
        errors.append(f"{label}: {exc}")
        return None


def parse_file(content: bytes, filename: str, directory: Directory, today: date) -> tuple[list[ImportRow], str, list[str]]:
    try:
        table = read_table(SPEC, content, filename, MAX_ROWS)
    except TemplateError as exc:
        raise OperationImportError(str(exc)) from exc
    accounts = {_norm(a.name): a for a in directory.accounts}
    active = directory.active_accounts()
    cats = {_norm(c.name): c for c in directory.categories}
    out: list[ImportRow] = []
    seen_ids: dict[uuid.UUID, int] = {}
    for t in table.rows:
        raw = t.values
        row = ImportRow(line=t.line, raw=raw)
        out.append(row)
        errors: list[str] = []
        op_text = _cell(raw, "op_id", lambda v: _text(v, 64), "ID", errors)
        if op_text:
            try:
                row.op_id = uuid.UUID(op_text.strip())
            except ValueError:
                errors.append("ID: не похож на ID из шаблона — очистите ячейку, чтобы добавить операцию как новую")
        row.day = _cell(raw, "day", parse_day, "дата", errors)
        income = _cell(raw, "income", parse_money, "приход", errors)
        expense = _cell(raw, "expense", parse_money, "расход", errors)
        signed = _cell(raw, "amount", parse_money, "сумма", errors)
        row.account = _cell(raw, "account", lambda v: _text(v, 100), "счёт", errors)
        row.category = _cell(raw, "category", lambda v: _text(v, 100), "статья", errors)
        row.counterparty = _cell(raw, "counterparty", lambda v: _text(v, 200), "контрагент", errors)
        row.comment = _cell(raw, "comment", lambda v: _text(v, 500), "комментарий", errors)

        filled = [v for v in (income, expense, signed) if v not in (None, Decimal(0))]
        if not filled and not errors:
            row.status, row.message = "skipped", SKIPPED_EMPTY
            continue
        if len(filled) > 1:
            errors.append("заполните что-то одно: «Приход» или «Расход»")
        elif filled:
            if (income is not None and income < 0) or (expense is not None and expense < 0):
                errors.append("приход и расход пишите без минуса — расход в колонку «Расход»")
            elif income:
                row.amount = income
            elif expense:
                row.amount = -expense
            else:
                row.amount = signed
            if row.amount is not None and abs(row.amount) >= svc.MAX_AMOUNT:
                errors.append("сумма: слишком большое число — проверьте")
            elif row.amount is not None:
                row.amount = row.amount.quantize(Decimal("0.01"))
        if row.day is None and "дата" not in " ".join(errors):
            errors.append("не указана дата")
        elif row.day is not None and row.day > today:
            errors.append("дата в будущем — здесь только то, что уже было")
        elif row.day is not None and row.day < svc.MIN_DAY:
            errors.append("дата: слишком ранняя — проверьте год")
        if row.account:
            acc = accounts.get(_norm(row.account))
            if acc is None:
                errors.append(f"счёта «{row.account}» нет — добавьте его в «Счетах» или выберите из списка")
            elif acc.archived_at is not None:
                errors.append(f"счёт «{acc.name}» в архиве")
            else:
                row.account_id = acc.id
        elif len(active) == 1:
            row.account_id, row.account = active[0].id, active[0].name  # один счёт — можно не указывать
        else:
            errors.append("укажите счёт")
        if row.category:
            cat = cats.get(_norm(row.category))
            if cat is None:
                errors.append(f"статьи «{row.category}» нет — добавьте её в «Статьях» или выберите из списка")
            elif cat.kind == "transfer":
                errors.append("переводы между своими счетами добавляйте на странице — «Перевод»")
            elif cat.archived_at is not None:
                errors.append(f"статья «{cat.name}» в архиве")
            else:
                row.category_id = cat.id
        if row.op_id is not None:
            if row.op_id in seen_ids:
                errors.append(f"этот ID уже есть в строке {seen_ids[row.op_id]} — у новой строки ID очистите")
            else:
                seen_ids[row.op_id] = t.line
        if errors:
            text = "; ".join(errors)
            row.status, row.message = "error", text[:1].upper() + text[1:]
    return out, table.format, table.columns


def _key(day, amount, account_id, category_id, counterparty, comment) -> tuple:
    return (day, Decimal(amount).quantize(Decimal("0.01")), account_id, category_id, counterparty or None, comment or None)


async def classify(db: AsyncSession, user_id: uuid.UUID, rows: list[ImportRow]) -> None:
    """new / changed / unchanged и проверка ID: операция есть, не выплата Ozon и не перевод."""
    current = (await db.execute(svc._current(select(MoneyOperation).where(MoneyOperation.user_id == user_id)))).scalars().all()
    by_id = {r.op_id: r for r in current}
    existing = Counter(_key(r.day, r.amount, r.account_id, r.category_id, r.counterparty, r.comment)
                       for r in current if r.transfer_id is None)
    for row in rows:
        if row.status in ("error", "skipped"):
            continue
        mine = _key(row.day, row.amount, row.account_id, row.category_id, row.counterparty, row.comment)
        if row.op_id is None:
            if existing[mine] > 0:
                existing[mine] -= 1
                row.status, row.message = "skipped", SKIPPED_DUPLICATE
            else:
                row.status = "new"
            continue
        op = by_id.get(row.op_id)
        if op is None:
            row.status = "error"
            row.message = "Операции с таким ID нет (удалена или из другого кабинета) — очистите ID, чтобы добавить её как новую"
        elif op.source == "ozon_payout" or op.transfer_id is not None:
            row.status = "error"
            row.message = "Это выплата Ozon или перевод — они меняются на странице, а не файлом"
        elif _key(op.day, op.amount, op.account_id, op.category_id, op.counterparty, op.comment) == mine:
            row.status = "unchanged"
        else:
            row.status = "changed"


async def import_operations(
    db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, apply: bool, today: date
) -> ImportResult:
    rows, _, _ = parse_file(content, filename, await _directory(db, user_id), today)
    await classify(db, user_id, rows)
    if not apply:
        return ImportResult(rows, applied=False)
    for row in rows:
        data = {"day": row.day, "amount": row.amount, "account_id": row.account_id, "category_id": row.category_id,
                "counterparty": row.counterparty, "comment": row.comment}
        if row.status == "new":
            await svc.create_operation(db, user_id, svc.OperationIn(**data), source="import")
        elif row.status == "changed":
            await svc.update_operation(db, user_id, row.op_id, data)
    await db.commit()
    return ImportResult(rows, applied=True)


async def template(
    db: AsyncSession, user_id: uuid.UUID, fmt: str, date_from: date | None, date_to: date | None, empty: bool
) -> bytes:
    """Шаблон с операциями периода (свои и из файла; без переводов и выплат Ozon) или пустой."""
    directory = await _directory(db, user_id)
    spec, columns = spec_for(directory)
    rows: list[dict[str, Any]] = []
    if not empty:
        q = svc._current(select(MoneyOperation).where(
            MoneyOperation.user_id == user_id, MoneyOperation.source.in_(("manual", "import")),
            MoneyOperation.transfer_id.is_(None),
        ))
        if date_from:
            q = q.where(MoneyOperation.day >= date_from)
        if date_to:
            q = q.where(MoneyOperation.day <= date_to)
        accounts = {a.id: a.name for a in directory.accounts}
        cats = {c.id: c.name for c in directory.categories}
        for op in (await db.execute(q.order_by(MoneyOperation.day, MoneyOperation.created_at))).scalars():
            amount = Decimal(op.amount)
            rows.append({
                "op_id": str(op.op_id), "day": op.day,
                "income": amount if amount > 0 else None, "expense": -amount if amount < 0 else None,
                "account": accounts.get(op.account_id), "category": cats.get(op.category_id),
                "counterparty": op.counterparty, "comment": op.comment,
            })
    return build_file(fmt, spec, rows, columns)


async def error_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, today: date) -> tuple[bytes, str]:
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: исправил → загрузил снова."""
    directory = await _directory(db, user_id)
    rows, fmt, found = parse_file(content, filename, directory, today)
    await classify(db, user_id, rows)
    errors = [r for r in rows if r.status == "error"]
    if not errors:
        raise OperationImportError("В файле нет строк с ошибками.")
    _, template_columns = spec_for(directory)
    extra = [c for c in SPEC.columns if not c.in_template and c.key in found]
    columns = [ERROR_COLUMN, *template_columns, *extra]
    out = []
    for r in errors:
        item = {c.key: echo(c.kind, r.raw.get(c.key)) for c in columns if not c.reference}
        item["_error"] = r.message
        out.append(item)
    return build_file(fmt, SPEC, out, columns), fmt
