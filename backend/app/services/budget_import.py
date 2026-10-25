"""Бюджет списком: шаблон со своими статьями и планом → заполнить → загрузить (шаг 10.21, правило 14).

Строка — статья и год, колонки — 12 месяцев. Файл, скачанный из сервиса, загружается обратно без ошибок. Пустая
ячейка месяца — плана нет (был — убираем), 0 — план «ноль». Изменение — новая версия плана месяца (правило 12).
"""
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import budget as eng
from app.models.planning import CashCategory
from app.schemas.planning import ImportRowOut
from app.services import budgets
from app.services import planning as svc
from app.services.operation_import import _cell, _norm, _safe_choices, _text
from app.services.templates import (
    ERROR_COLUMN,
    CellError,
    Column,
    TemplateError,
    TemplateSpec,
    build_file,
    echo,
    is_empty,
    parse_money,
    read_table,
)

MAX_ROWS = 2_000
Status = Literal["new", "changed", "unchanged", "skipped", "error"]
KIND_NAMES = {"income": "Поступление", "expense": "Расход"}
MONTH_KEYS = [f"m{m:02d}" for m in range(1, 13)]

SPEC = TemplateSpec(
    sheet="Бюджет",
    columns=(
        Column("category", "Статья", width=28, aliases=("статья", "категори"),
               hint="Как в разделе «Статьи». Подстатья — отдельной строкой: её план входит в статью."),
        Column("kind", "Вид", width=14, aliases=("вид", "тип"), choices=tuple(KIND_NAMES.values()),
               hint="Поступление или расход — по статье. Нужен, если у поступления и расхода одинаковые названия."),
        Column("parent", "Входит в статью", width=22, reference=True, aliases=("входит в",),
               hint="Справочно: статья, в которую входит подстатья. При загрузке не читается."),
        Column("year", "Год", width=8, aliases=("год",), hint="Год плана, например 2026."),
        *(Column(key, eng.MONTHS[i].capitalize(), kind="money", width=13, aliases=(eng.MONTHS[i][:4],),
                 hint="План на месяц, руб., без минуса. Пусто — плана нет." if i == 0 else "")
          for i, key in enumerate(MONTH_KEYS)),
    ),
    required=(("category",), ("year",)),
    missing_columns=(
        "Не найдены колонки «Статья» и «Год». Скачайте шаблон на странице «Операции → Бюджеты и сценарии» — или назовите "
        "колонки так же: статья, вид, год, январь … декабрь."
    ),
    instructions=(
        "1. Одна строка — одна статья на год: план по месяцам. Квартал и год — суммы месяцев.",
        "2. Сумма — без минуса: поступление или расход — по статье. Пусто — плана нет; 0 — план «ноль».",
        "3. Сохраните файл и загрузите на странице «Операции → Бюджеты и сценарии». Сначала покажем, что изменится, — "
        "применится только после подтверждения. Прежний план остаётся в истории.",
        "В бюджете — статьи с отметкой «Бюджеты» (раздел «Статьи»). Факт считается по операциям сам.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и не удаляйте строку заголовков — по ним мы находим колонки.",
        "• Пример: «Аренда», Расход, 2026, по 30 000 в каждом месяце.",
    ),
)


class BudgetImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


@dataclass
class ImportRow:
    line: int
    category: str | None = None
    amount: Decimal | None = None  # план за год по строке
    comment: str | None = None
    status: Status = "new"
    message: str | None = None
    values: dict[tuple[uuid.UUID, date], Decimal | None] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ImportResult:
    rows: list[ImportRow]
    applied: bool

    def count(self, status: Status) -> int:
        return sum(r.status == status for r in self.rows)


def row_out(r: ImportRow) -> ImportRowOut:
    return ImportRowOut(line=r.line, op_id=None, day=None, amount=r.amount, account=None, category=r.category,
                        counterparty=None, comment=r.comment, status=r.status, message=r.message)


def parse_year(value: Any) -> int:
    if isinstance(value, (int, float, Decimal)) and float(value).is_integer():
        year = int(value)
    else:
        text = str(value).strip().rstrip(".").replace(" ", "")
        if not text.isdigit():
            raise CellError("нужен год числом, например 2026")
        year = int(text)
    if not 2000 <= year <= 2100:
        raise CellError("год — от 2000 до 2100")
    return year


def parse_kind(value: Any) -> str | None:
    if is_empty(value):
        return None
    text = _norm(str(value))
    if text.startswith(("пост", "приход", "доход")):
        return "income"
    if text.startswith(("расх", "плат", "трат")):
        return "expense"
    raise CellError("нужно «Поступление» или «Расход»")


def parse_file(content: bytes, filename: str, cats: list[CashCategory]) -> tuple[list[ImportRow], str, list[str]]:
    try:
        table = read_table(SPEC, content, filename, MAX_ROWS)
    except TemplateError as exc:
        raise BudgetImportError(str(exc)) from exc
    months_present = [k for k in MONTH_KEYS if k in table.columns]
    by_name: dict[str, list[CashCategory]] = {}
    for c in cats:
        by_name.setdefault(_norm(c.name), []).append(c)
    out: list[ImportRow] = []
    seen: dict[tuple, int] = {}
    for t in table.rows:
        raw = t.values
        row = ImportRow(line=t.line, raw=raw)
        out.append(row)
        errors: list[str] = []
        row.category = _cell(raw, "category", lambda v: _text(v, 100), "статья", errors)
        kind = _cell(raw, "kind", parse_kind, "вид", errors)
        year = _cell(raw, "year", parse_year, "год", errors)
        amounts = {k: _cell(raw, k, parse_money, eng.MONTHS[MONTH_KEYS.index(k)], errors) for k in months_present}
        if not row.category and not errors:
            row.status, row.message = "skipped", "Статья не указана — строка пропущена"
            continue
        if year is None and not any(e.startswith("год") for e in errors):
            errors.append("не указан год")
        cat = None
        if row.category:
            found = [c for c in by_name.get(_norm(row.category), []) if kind is None or c.kind == kind]
            if not found:
                errors.append(f"статьи «{row.category}» нет среди статей бюджета — добавьте её в «Статьях» и отметьте "
                              "«Бюджеты» или выберите из списка")
            elif len(found) > 1:
                errors.append("такая статья есть и в поступлениях, и в расходах — укажите «Вид»")
            else:
                cat = found[0]
        for k, v in amounts.items():
            if v is not None and v < 0:
                errors.append(f"{eng.MONTHS[MONTH_KEYS.index(k)]}: план без минуса")
            elif v is not None and v >= svc.MAX_AMOUNT:
                errors.append(f"{eng.MONTHS[MONTH_KEYS.index(k)]}: слишком большое число")
        if cat is not None and year is not None:
            if (cat.id, year) in seen:
                errors.append(f"статья «{cat.name}» за {year} уже есть в строке {seen[(cat.id, year)]}")
            else:
                seen[(cat.id, year)] = t.line
        if errors:
            text = "; ".join(errors)
            row.status, row.message = "error", text[:1].upper() + text[1:]
            continue
        row.category = cat.name
        row.values = {(cat.id, date(year, MONTH_KEYS.index(k) + 1, 1)): (None if v is None else v.quantize(Decimal("0.01")))
                      for k, v in amounts.items()}
        row.amount = eng.sum_known(row.values.values())
    return out, table.format, table.columns


async def classify(db: AsyncSession, user_id: uuid.UUID, rows: list[ImportRow]) -> None:
    keys = [k for r in rows if r.status not in ("error", "skipped") for k in r.values]
    if not keys:
        return
    months = [m for _, m in keys]
    current = await budgets.current_lines(db, user_id, min(months), max(months))
    for row in rows:
        if row.status in ("error", "skipped"):
            continue
        before = {k: (None if (line := current.get(k)) is None or line.amount is None else Decimal(line.amount))
                  for k in row.values}
        changes = sum(before[k] != v for k, v in row.values.items())
        if not any(v is not None for v in before.values()) and not any(v is not None for v in row.values.values()):
            row.status, row.message = "skipped", "Плана нет ни в одном месяце — строка пропущена"
        elif not any(v is not None for v in before.values()):
            row.status, row.comment = "new", f"месяцев с планом: {sum(v is not None for v in row.values.values())}"
        elif changes:
            row.status, row.comment = "changed", f"изменится месяцев: {changes}"
        else:
            row.status = "unchanged"


async def import_budget(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, apply: bool) -> ImportResult:
    rows, _, _ = parse_file(content, filename, await budgets.categories_for_template(db, user_id))
    await classify(db, user_id, rows)
    if not apply:
        return ImportResult(rows, applied=False)
    values = {k: v for r in rows if r.status in ("new", "changed") for k, v in r.values.items()}
    await budgets.write(db, user_id, values, "import")
    await db.commit()
    return ImportResult(rows, applied=True)


def _columns(cats: list[CashCategory]) -> list[Column]:
    names = _safe_choices(sorted({c.name for c in cats}))
    return [replace(c, choices=names) if c.key == "category" else c for c in SPEC.template_columns]


async def template(db: AsyncSession, user_id: uuid.UUID, fmt: str, empty: bool, year: int) -> bytes:
    """Шаблон со статьями бюджета и планом на год (пустой — статьи без сумм)."""
    cats = await budgets.categories_for_template(db, user_id)
    names = {c.id: c.name for c in await svc._categories(db, user_id)}
    plans = {} if empty else await budgets.current_lines(db, user_id, date(year, 1, 1), date(year, 12, 1))
    rows = []
    for c in cats:
        item: dict[str, Any] = {"category": c.name, "kind": KIND_NAMES[c.kind], "parent": names.get(c.parent_id),
                                "year": year}
        for i, key in enumerate(MONTH_KEYS):
            line = plans.get((c.id, date(year, i + 1, 1)))
            item[key] = None if line is None or line.amount is None else Decimal(line.amount)
        rows.append(item)
    return build_file(fmt, SPEC, rows, _columns(cats))


async def error_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> tuple[bytes, str]:
    cats = await budgets.categories_for_template(db, user_id)
    rows, fmt, _ = parse_file(content, filename, cats)
    await classify(db, user_id, rows)
    errors = [r for r in rows if r.status == "error"]
    if not errors:
        raise BudgetImportError("В файле нет строк с ошибками.")
    columns = [ERROR_COLUMN, *_columns(cats)]
    out = []
    for r in errors:
        item = {c.key: echo(c.kind, r.raw.get(c.key)) for c in columns if not c.reference}
        item["_error"] = r.message
        out.append(item)
    return build_file(fmt, SPEC, out, columns), fmt
