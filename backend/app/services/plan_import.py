"""Плановые платежи списком: шаблон со своими планами → заполнить → загрузить (шаг 3.9, правило 14).

На общем модуле `services/templates`, как операции (operation_import): файл, скачанный из сервиса, загружается
обратно без ошибок. Строка с ID — существующий план: изменился — новая версия (правило 12), нет — «без изменений».
Строка без ID — новый план; такой же уже есть — пропускается, чтобы повторная загрузка файла не задвоила план.
Закупки с отсрочкой в шаблон не входят — их дата считается от закупки, добавляются в календаре.
"""
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.planning import ImportRowOut
from app.services import payment_plan as pp
from app.services import planning as svc
from app.services.operation_import import Directory, _cell, _directory, _norm, _safe_choices, _text
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
PERIOD_NAMES = {"once": "разово", "week": "раз в неделю", "month": "раз в месяц", "quarter": "раз в квартал",
                "year": "раз в год"}
PERIOD_WORDS = (("недел", "week"), ("месяц", "month"), ("ежемесяч", "month"), ("квартал", "quarter"),
                ("год", "year"), ("разов", "once"), ("однократ", "once"), ("один раз", "once"))
WEEKDAY_WORDS = (("пн", 1), ("понед", 1), ("вт", 2), ("ср", 3), ("чт", 4), ("четв", 4), ("пт", 5), ("пятн", 5),
                 ("сб", 6), ("суб", 6), ("вс", 7), ("воскр", 7))
YES = ("да", "yes", "1", "true", "вкл", "включ", "+")
NO = ("нет", "no", "0", "false", "выкл", "выключ", "-")
SKIPPED_EMPTY = "Сумма не указана — строка пропущена"
SKIPPED_DUPLICATE = "Такой план уже есть — не добавляем второй раз"

SPEC = TemplateSpec(
    sheet="Плановые платежи",
    columns=(
        Column("plan_id", "ID (не меняйте)", width=38, aliases=("id плана", "id"),
               hint="Пусто — новый план. С ID — изменение этого плана: ID не меняйте и не копируйте в новые строки."),
        Column("title", "Название", width=28, aliases=("название", "наименование", "платеж", "платёж"),
               hint="Например, «Аренда склада». Пусто — по статье."),
        Column("income", "Приход, руб.", kind="money", width=15, aliases=("приход", "поступлени"),
               hint="Сколько придёт — число больше 0. В строке заполните либо «Приход», либо «Расход»."),
        Column("expense", "Расход, руб.", kind="money", width=15, aliases=("расход", "платеж, руб", "списани"),
               hint="Сколько уйдёт — число больше 0, без минуса."),
        Column("period", "Периодичность", width=16, aliases=("периодичн", "повтор", "как часто"),
               choices=tuple(PERIOD_NAMES.values()),
               hint="Разово, раз в неделю, раз в месяц, раз в квартал или раз в год. Пусто — разово."),
        Column("day", "День", width=10, aliases=("день", "число"),
               hint="Для «раз в неделю» — день недели (пн, вт… или 1–7), для остальных — число месяца 1–31 "
                    "(31 — последний день). Пусто — как у даты начала."),
        Column("start", "Дата / начало", kind="date", width=14, aliases=("дата / начало", "начало", "дата"),
               exclude=("окончан",),
               hint="Разовый — дата платежа; регулярный — с какой даты, ДД.ММ.ГГГГ."),
        Column("end", "Окончание", kind="date", width=14, aliases=("окончан", "конец"),
               hint="Необязательно: до какой даты повторять. Пусто — без конца."),
        Column("account", "Счёт", width=22, aliases=("счёт", "счет", "кошел"),
               hint="Необязательно: с какого счёта платите или куда придёт — как в «Счетах»."),
        Column("category", "Статья", width=26, aliases=("статья", "категори"),
               hint="Как в разделе «Статьи». Пусто — без статьи."),
        Column("counterparty", "Контрагент", width=24, aliases=("контрагент", "получатель", "плательщик"),
               hint="Необязательно: кому платите или от кого придёт — по нему узнаем оплату в операциях."),
        Column("comment", "Описание", width=36, aliases=("описание", "комментарий", "примечание")),
        Column("enabled", "Включён", width=10, aliases=("включ", "актив"), choices=("да", "нет"),
               hint="«нет» — план есть, но в прогнозе не участвует («что если»). Пусто — да."),
    ),
    required=(("start",), ("income", "expense")),
    missing_columns=(
        "Не найдены колонки «Дата / начало» и «Приход» / «Расход». Скачайте шаблон на странице «Планирование → "
        "Календарь» — или назовите колонки так же: дата, приход, расход, периодичность, день."
    ),
    instructions=(
        "1. Одна строка — один плановый платёж: разовый или регулярный (аренда, зарплата, кредит, подписки).",
        "2. Сумма — в «Приход» или «Расход», без минуса. Периодичность и день — как часто и какого числа.",
        "3. Строки с ID — ваши планы: исправьте их здесь, ID не трогайте. Изменение сохранится новой версией плана.",
        "4. Сохраните файл и загрузите на странице «Планирование → Календарь». Сначала покажем, что изменится, — "
        "применится только после подтверждения.",
        "Выплаты Ozon, налоги и оплату поставщику после продажи вносить не нужно — они появляются в календаре сами.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и не удаляйте строку заголовков — по ним мы находим колонки.",
        "• Пример: «Аренда склада», расход 30 000, раз в месяц, день 5, начало 01.11.2026, статья «Аренда».",
    ),
)


class PlanImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


@dataclass
class ImportRow:
    line: int
    op_id: uuid.UUID | None = None  # ID плана — под общим ответом импорта
    day: date | None = None  # дата / начало
    amount: Decimal | None = None
    account: str | None = None
    category: str | None = None
    counterparty: str | None = None
    comment: str | None = None
    status: Status = "new"
    message: str | None = None
    data: pp.PlanIn | None = field(default=None, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ImportResult:
    rows: list[ImportRow]
    applied: bool

    def count(self, status: Status) -> int:
        return sum(r.status == status for r in self.rows)


def row_out(r: ImportRow) -> ImportRowOut:
    return ImportRowOut(line=r.line, op_id=r.op_id, day=r.day, amount=r.amount, account=r.account, category=r.category,
                        counterparty=r.counterparty, comment=r.comment, status=r.status, message=r.message)


def spec_for(directory: Directory) -> list[Column]:
    accounts = _safe_choices([a.name for a in directory.active_accounts()])
    cats = _safe_choices([c.name for c in directory.categories if c.archived_at is None and c.kind != "transfer"])
    return [
        replace(c, choices=accounts) if c.key == "account" else replace(c, choices=cats) if c.key == "category" else c
        for c in SPEC.template_columns
    ]


def parse_period(value: Any) -> str:
    text = _norm(str(value)) if not is_empty(value) else ""
    if not text:
        return "once"
    for word, period in PERIOD_WORDS:
        if word in text:
            return period
    raise CellError("нужно: разово, раз в неделю, раз в месяц, раз в квартал или раз в год")


def parse_weekday_or_day(value: Any) -> int | None:
    if is_empty(value):
        return None
    if isinstance(value, (int, float, Decimal)) and float(value).is_integer():
        return int(value)
    text = _norm(str(value)).rstrip(".")
    if text.isdigit():
        return int(text)
    for word, day in WEEKDAY_WORDS:
        if text.startswith(word):
            return day
    raise CellError("нужно число месяца 1–31 или день недели (пн, вт… или 1–7)")


def parse_flag(value: Any) -> bool:
    if is_empty(value):
        return True
    text = _norm(str(value))
    if any(text.startswith(w) for w in NO):
        return False
    if any(text.startswith(w) for w in YES):
        return True
    raise CellError("нужно «да» или «нет»")


def parse_file(content: bytes, filename: str, directory: Directory) -> tuple[list[ImportRow], str, list[str]]:
    try:
        table = read_table(SPEC, content, filename, MAX_ROWS)
    except TemplateError as exc:
        raise PlanImportError(str(exc)) from exc
    accounts = {_norm(a.name): a for a in directory.accounts}
    cats = {_norm(c.name): c for c in directory.categories}
    out: list[ImportRow] = []
    seen: dict[uuid.UUID, int] = {}
    for t in table.rows:
        raw = t.values
        row = ImportRow(line=t.line, raw=raw)
        out.append(row)
        errors: list[str] = []
        id_text = _cell(raw, "plan_id", lambda v: _text(v, 64), "ID", errors)
        if id_text:
            try:
                row.op_id = uuid.UUID(id_text.strip())
            except ValueError:
                errors.append("ID: не похож на ID из шаблона — очистите ячейку, чтобы добавить план как новый")
        title = _cell(raw, "title", lambda v: _text(v, 200), "название", errors)
        income = _cell(raw, "income", parse_money, "приход", errors)
        expense = _cell(raw, "expense", parse_money, "расход", errors)
        period = _cell(raw, "period", parse_period, "периодичность", errors) or "once"
        day = _cell(raw, "day", parse_weekday_or_day, "день", errors)
        row.day = _cell(raw, "start", parse_day, "дата", errors)
        end = _cell(raw, "end", parse_day, "окончание", errors)
        row.account = _cell(raw, "account", lambda v: _text(v, 100), "счёт", errors)
        row.category = _cell(raw, "category", lambda v: _text(v, 100), "статья", errors)
        row.counterparty = _cell(raw, "counterparty", lambda v: _text(v, 200), "контрагент", errors)
        row.comment = _cell(raw, "comment", lambda v: _text(v, 2000), "описание", errors)
        enabled = _cell(raw, "enabled", parse_flag, "включён", errors)

        filled = [v for v in (income, expense) if v not in (None, Decimal(0))]
        if not filled and not errors:
            row.status, row.message = "skipped", SKIPPED_EMPTY
            continue
        kind = None
        if len(filled) > 1:
            errors.append("заполните что-то одно: «Приход» или «Расход»")
        elif filled:
            value = filled[0]
            if value < 0:
                errors.append("приход и расход пишите без минуса — расход в колонку «Расход»")
            elif abs(value) >= svc.MAX_AMOUNT:
                errors.append("сумма: слишком большое число — проверьте")
            else:
                kind = "income" if income else "expense"
                row.amount = value.quantize(Decimal("0.01")) * (1 if kind == "income" else -1)
        if row.day is None and "дата" not in " ".join(errors):
            errors.append("не указана дата / начало")
        elif row.day is not None and row.day < svc.MIN_DAY:
            errors.append("дата: слишком ранняя — проверьте год")
        if day is not None and (day < 1 or day > (7 if period == "week" else 31)):
            errors.append("день недели — от 1 до 7" if period == "week" else "число месяца — от 1 до 31")
        if end is not None and row.day is not None and end < row.day:
            errors.append("окончание раньше начала")
        account_id = category_id = None
        if row.account:
            acc = accounts.get(_norm(row.account))
            if acc is None:
                errors.append(f"счёта «{row.account}» нет — добавьте его в «Счетах» или выберите из списка")
            elif acc.archived_at is not None:
                errors.append(f"счёт «{acc.name}» в архиве")
            else:
                account_id = acc.id
        if row.category:
            cat = cats.get(_norm(row.category))
            if cat is None:
                errors.append(f"статьи «{row.category}» нет — добавьте её в «Статьях» или выберите из списка")
            elif cat.kind == "transfer":
                errors.append("перевод между своими счетами не планируется")
            elif cat.archived_at is not None:
                errors.append(f"статья «{cat.name}» в архиве")
            elif kind is not None and cat.kind != kind:
                errors.append(f"статья «{cat.name}» — для {'поступлений' if cat.kind == 'income' else 'расходов'}")
            else:
                category_id = cat.id
        if row.op_id is not None:
            if row.op_id in seen:
                errors.append(f"этот ID уже есть в строке {seen[row.op_id]} — у новой строки ID очистите")
            else:
                seen[row.op_id] = t.line
        if errors:
            text = "; ".join(errors)
            row.status, row.message = "error", text[:1].upper() + text[1:]
            continue
        row.data = pp.PlanIn(
            kind=kind, amount=abs(row.amount), start_date=row.day, title=title or None, period=period,
            day=day if period != "once" else None, end_date=end if period != "once" else None,
            category_id=category_id, account_id=account_id, counterparty=row.counterparty, comment=row.comment,
            enabled=True if enabled is None else enabled,
        )
    return out, table.format, table.columns


def _key(p) -> tuple:
    return (p.kind, p.title, Decimal(p.amount).quantize(Decimal("0.01")), p.period, p.day, p.start_date, p.end_date,
            p.category_id, p.account_id, p.counterparty or None, p.comment or None, bool(p.enabled))


async def classify(db: AsyncSession, user_id: uuid.UUID, rows: list[ImportRow]) -> None:
    current = {r.plan_id: r for r in await pp.plans(db, user_id)}
    existing = Counter(_key(r) for r in current.values())
    for row in rows:
        if row.status in ("error", "skipped"):
            continue
        try:
            row.data = await pp.check_plan(db, user_id, row.data, keep=current.get(row.op_id))
        except svc.PlanningError as exc:
            row.status, row.message = "error", str(exc)
            continue
        mine = _key(row.data)
        if row.op_id is None:
            if existing[mine] > 0:
                existing[mine] -= 1
                row.status, row.message = "skipped", SKIPPED_DUPLICATE
            else:
                row.status = "new"
            continue
        plan = current.get(row.op_id)
        if plan is None:
            row.status = "error"
            row.message = "Плана с таким ID нет (удалён или из другого кабинета) — очистите ID, чтобы добавить его как новый"
        elif plan.purchase_day is not None:
            row.status, row.message = "error", "Это закупка с отсрочкой — она меняется в календаре, а не файлом"
        elif _key(plan) == mine:
            row.status = "unchanged"
        else:
            row.status = "changed"


async def import_plans(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, apply: bool) -> ImportResult:
    rows, _, _ = parse_file(content, filename, await _directory(db, user_id))
    await classify(db, user_id, rows)
    if not apply:
        return ImportResult(rows, applied=False)
    for row in rows:
        if row.status == "new":
            await pp.create_plan(db, user_id, row.data, source="import")
        elif row.status == "changed":
            await pp.update_plan(db, user_id, row.op_id, {k: getattr(row.data, k) for k in pp.FIELDS})
    await db.commit()
    return ImportResult(rows, applied=True)


async def template(db: AsyncSession, user_id: uuid.UUID, fmt: str, empty: bool) -> bytes:
    """Шаблон с вашими планами (без закупок с отсрочкой) или пустой."""
    directory = await _directory(db, user_id)
    columns = spec_for(directory)
    rows: list[dict[str, Any]] = []
    if not empty:
        accounts = {a.id: a.name for a in directory.accounts}
        cats = {c.id: c.name for c in directory.categories}
        for p in await pp.plans(db, user_id):
            if p.purchase_day is not None:
                continue
            amount = Decimal(p.amount)
            rows.append({
                "plan_id": str(p.plan_id), "title": p.title,
                "income": amount if p.kind == "income" else None, "expense": amount if p.kind == "expense" else None,
                "period": PERIOD_NAMES[p.period], "day": p.day, "start": p.start_date, "end": p.end_date,
                "account": accounts.get(p.account_id), "category": cats.get(p.category_id),
                "counterparty": p.counterparty, "comment": p.comment, "enabled": "да" if p.enabled else "нет",
            })
    return build_file(fmt, SPEC, rows, columns)


async def error_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> tuple[bytes, str]:
    directory = await _directory(db, user_id)
    rows, fmt, _ = parse_file(content, filename, directory)
    await classify(db, user_id, rows)
    errors = [r for r in rows if r.status == "error"]
    if not errors:
        raise PlanImportError("В файле нет строк с ошибками.")
    columns = [ERROR_COLUMN, *spec_for(directory)]
    out = []
    for r in errors:
        item = {c.key: echo(c.kind, r.raw.get(c.key)) for c in columns if not c.reference}
        item["_error"] = r.message
        out.append(item)
    return build_file(fmt, SPEC, out, columns), fmt
