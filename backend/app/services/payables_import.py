"""Закупки и оплаты поставщику списком: шаблон со своими записями → заполнить → загрузить (шаг 10.29, правило 14).

На общем модуле `services/templates`, как плановые платежи (plan_import): файл, скачанный из сервиса, загружается обратно
без ошибок. Строка с ID — существующая запись: изменилась — новая версия (правило 12), нет — «без изменений». Строка без
ID — новая; такая же уже есть — пропускается, чтобы повторная загрузка не задвоила долг. Начало учёта в файл не входит —
оно задаётся на странице.
"""
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import payables as svc
from app.services.operation_import import _cell, _norm, _text
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

MAX_ROWS = 5_000
Status = Literal["new", "changed", "unchanged", "skipped", "error"]
KIND_NAMES = {"purchase": "закупка", "payment": "оплата"}
SKIPPED_EMPTY = "Сумма не указана — строка пропущена"
SKIPPED_DUPLICATE = "Такая запись уже есть — не добавляем второй раз"

SPEC = TemplateSpec(
    sheet="Закупки и оплаты",
    columns=(
        Column("entry_id", "ID (не меняйте)", width=38, aliases=("id записи", "id"),
               hint="Пусто — новая запись. С ID — изменение этой записи: ID не меняйте и не копируйте в новые строки."),
        Column("kind", "Вид", width=12, aliases=("вид", "тип"), choices=tuple(KIND_NAMES.values()),
               hint="«закупка» — товар получен с отсрочкой оплаты; «оплата» — вы заплатили поставщику."),
        Column("day", "Дата", kind="date", width=14, aliases=("дата",), exclude=("срок",),
               hint="Дата закупки (по накладной) или оплаты, ДД.ММ.ГГГГ. Не раньше начала учёта и не позже сегодня."),
        Column("amount", "Сумма с НДС, руб.", kind="money", width=18, aliases=("сумма",),
               hint="Число больше 0, без минуса. У закупки — по накладной с НДС."),
        Column("due", "Срок оплаты", kind="date", width=14, aliases=("срок", "оплатить до"),
               hint="Только у закупки: до какого дня нужно заплатить. Пусто — дата закупки + отсрочка из настроек."),
        Column("number", "№ документа", width=16, aliases=("документ", "номер", "накладн", "упд"),
               hint="Необязательно: номер накладной или УПД."),
        Column("counterparty", "Контрагент", width=26, aliases=("контрагент", "поставщик"),
               hint="Необязательно: название поставщика."),
        Column("comment", "Описание", width=36, aliases=("описание", "комментарий", "примечание")),
    ),
    required=(("day",), ("amount",)),
    missing_columns=(
        "Не найдены колонки «Дата» и «Сумма». Скачайте шаблон на странице «Финансы → Долги поставщикам» — "
        "или назовите колонки так же: вид, дата, сумма, срок оплаты."
    ),
    instructions=(
        "1. Одна строка — одна закупка с отсрочкой или одна оплата поставщику.",
        "2. Вид: «закупка» или «оплата». Сумма — с НДС, без минуса.",
        "3. Строки с ID — ваши записи: исправьте их здесь, ID не трогайте. Изменение сохранится новой версией записи.",
        "4. Сохраните файл и загрузите на странице «Финансы → Долги поставщикам». Сначала покажем, что изменится, — "
        "применится только после подтверждения.",
        "Оплаты, которые вы ведёте в «Операциях» (статья «Оплата поставщику»), вносить не нужно — они засчитываются сами.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и не удаляйте строку заголовков — по ним мы находим колонки.",
        "• Даты раньше начала учёта не принимаются: то, что было до него, уже входит в долг на начало.",
    ),
)


class PayablesImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


def parse_kind(value: Any) -> str:
    if is_empty(value):
        raise CellError("укажите вид: «закупка» или «оплата»")
    text = _norm(str(value))
    if "закуп" in text or "поступлен" in text or "накладн" in text:
        return "purchase"
    if "оплат" in text or "платеж" in text or "платёж" in text:
        return "payment"
    raise CellError("вид — «закупка» или «оплата»")


@dataclass
class ImportRow:
    line: int
    entry_id: uuid.UUID | None = None
    kind: str | None = None
    day: date | None = None
    amount: Decimal | None = None
    due_day: date | None = None
    number: str | None = None
    counterparty: str | None = None
    comment: str | None = None
    status: Status = "new"
    message: str | None = None
    data: dict | None = field(default=None, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ImportResult:
    rows: list[ImportRow]
    applied: bool

    def count(self, status: Status) -> int:
        return sum(r.status == status for r in self.rows)


def row_out(r: ImportRow) -> dict:
    return {"line": r.line, "entry_id": r.entry_id, "kind": r.kind, "day": r.day, "amount": r.amount, "due_day": r.due_day,
            "number": r.number, "counterparty": r.counterparty, "comment": r.comment, "status": r.status,
            "message": r.message}


def parse_file(content: bytes, filename: str) -> tuple[list[ImportRow], str]:
    try:
        table = read_table(SPEC, content, filename, MAX_ROWS)
    except TemplateError as exc:
        raise PayablesImportError(str(exc)) from exc
    out: list[ImportRow] = []
    seen: dict[uuid.UUID, int] = {}
    for t in table.rows:
        raw = t.values
        row = ImportRow(line=t.line, raw=raw)
        out.append(row)
        errors: list[str] = []
        id_text = _cell(raw, "entry_id", lambda v: _text(v, 64), "ID", errors)
        if id_text:
            try:
                row.entry_id = uuid.UUID(id_text.strip())
            except ValueError:
                errors.append("ID: не похож на ID из шаблона — очистите ячейку, чтобы добавить запись как новую")
        row.kind = _cell(raw, "kind", parse_kind, "вид", errors)
        row.day = _cell(raw, "day", parse_day, "дата", errors)
        row.amount = _cell(raw, "amount", parse_money, "сумма", errors)
        row.due_day = _cell(raw, "due", parse_day, "срок оплаты", errors)
        row.number = _cell(raw, "number", lambda v: _text(v, 100), "№ документа", errors)
        row.counterparty = _cell(raw, "counterparty", lambda v: _text(v, 200), "контрагент", errors)
        row.comment = _cell(raw, "comment", lambda v: _text(v, 2000), "описание", errors)
        if row.amount in (None, Decimal(0)) and not errors:
            row.status, row.message = "skipped", SKIPPED_EMPTY
            continue
        if row.amount is not None and row.amount < 0:
            errors.append("сумму пишите без минуса")
        if row.day is None and not any(e.startswith("дата") for e in errors):
            errors.append("не указана дата")
        if row.due_day is not None and row.kind == "payment":
            errors.append("срок оплаты — только у закупки")
        if row.entry_id is not None:
            if row.entry_id in seen:
                errors.append(f"этот ID уже есть в строке {seen[row.entry_id]} — у новой строки ID очистите")
            else:
                seen[row.entry_id] = t.line
        if errors:
            text = "; ".join(errors)
            row.status, row.message = "error", text[:1].upper() + text[1:]
            continue
        row.data = {"day": row.day, "amount": row.amount, "due_day": row.due_day, "number": row.number,
                    "counterparty": row.counterparty, "comment": row.comment}
    return out, table.format


def _key(kind: str, d: dict) -> tuple:
    return (kind, d["day"], Decimal(d["amount"]).quantize(Decimal("0.01")), d.get("due_day"), d.get("number") or None,
            d.get("counterparty") or None, d.get("comment") or None)


async def classify(db: AsyncSession, user_id: uuid.UUID, rows: list[ImportRow]) -> None:
    current = {e.entry_id: e for e in await svc.entries(db, user_id) if e.kind != "opening"}
    existing = Counter(_key(e.kind, vars_of(e)) for e in current.values())
    us = await svc.require_postpay(db, user_id)
    for row in rows:
        if row.status in ("error", "skipped"):
            continue
        entry = current.get(row.entry_id) if row.entry_id else None
        kind = row.kind if entry is None else entry.kind
        if entry is not None and row.kind != entry.kind:
            row.status, row.message = "error", "Вид записи не меняется: удалите её и добавьте новую"
            continue
        try:
            row.data = await svc.check_entry(db, user_id, kind, row.data, us)
        except svc.PayablesError as exc:
            row.status, row.message = "error", str(exc)
            continue
        mine = _key(kind, row.data)
        if row.entry_id is None:
            if existing[mine] > 0:
                existing[mine] -= 1
                row.status, row.message = "skipped", SKIPPED_DUPLICATE
            else:
                row.status = "new"
        elif entry is None:
            row.status = "error"
            row.message = "Записи с таким ID нет (удалена или из другого кабинета) — очистите ID, чтобы добавить её как новую"
        elif _key(entry.kind, vars_of(entry)) == mine:
            row.status = "unchanged"
        else:
            row.status = "changed"


def vars_of(e) -> dict:
    return {"day": e.day, "amount": e.amount, "due_day": e.due_day, "number": e.number, "counterparty": e.counterparty,
            "comment": e.comment}


async def import_entries(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, apply: bool) -> ImportResult:
    rows, _ = parse_file(content, filename)
    await classify(db, user_id, rows)
    if not apply:
        return ImportResult(rows, applied=False)
    for row in rows:
        if row.status == "new":
            await svc.create_entry(db, user_id, row.kind, row.data, source="import")
        elif row.status == "changed":
            await svc.update_entry(db, user_id, row.entry_id, row.data)
    await db.commit()
    return ImportResult(rows, applied=True)


async def template(db: AsyncSession, user_id: uuid.UUID, fmt: str, empty: bool) -> bytes:
    await svc.require_postpay(db, user_id)
    rows: list[dict[str, Any]] = []
    if not empty:
        for e in await svc.entries(db, user_id):
            if e.kind == "opening":
                continue
            rows.append({"entry_id": str(e.entry_id), "kind": KIND_NAMES[e.kind], "day": e.day, "amount": e.amount,
                         "due": e.due_day, "number": e.number, "counterparty": e.counterparty, "comment": e.comment})
    return build_file(fmt, SPEC, rows, list(SPEC.template_columns))


async def error_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> tuple[bytes, str]:
    rows, fmt = parse_file(content, filename)
    await classify(db, user_id, rows)
    errors = [r for r in rows if r.status == "error"]
    if not errors:
        raise PayablesImportError("В файле нет строк с ошибками.")
    columns = [ERROR_COLUMN, *SPEC.template_columns]
    out = []
    for r in errors:
        item = {c.key: echo(c.kind, r.raw.get(c.key)) for c in columns if not c.reference}
        item["_error"] = r.message
        out.append(item)
    return build_file(fmt, SPEC, out, columns), fmt
