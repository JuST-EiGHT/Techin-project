"""Себестоимость из файла: шаблон со своими товарами → заполнить → загрузить (шаги 2.3–2.4).

Шаблон и разбор — на общем модуле `services/templates` (правило 14): файл, скачанный из сервиса,
загружается обратно без ошибок. Пустая себестоимость — «пропущено», такая же цена — «без
изменений». Каждая строка проверяется отдельно: ошибки не мешают применить остальные строки.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.xlsx.common import cell_text
from app.services.costs import CostHistory, add_cost, known_articles, load_cost_history
from app.services.pnl_stale import affected_period, invalidate
from app.services.report_costs import recost_reports
from app.services.sync.finance import today_msk
from app.services.tax_profile import supplier_vat_multiplier
from app.services.templates import (
    ERROR_COLUMN,
    RATE_CHOICES,
    CellError,
    Column,
    TemplateError,
    TemplateSpec,
    build_file,
    echo,
    is_empty,
    parse_day,
    parse_key,
    parse_money,
    parse_rate,
    rate_text,
    read_table,
)

MAX_ROWS = 20_000
MAX_COST = Decimal("100000000")

COST_TITLE = "Себестоимость за шт. без НДС, руб."
COST_SPEC = TemplateSpec(
    sheet="Себестоимость",
    columns=(
        Column(
            "offer_id",
            "Артикул",
            width=28,
            aliases=("offer_id", "артикул продавца", "артикул", "article"),
            exclude=("справочно",),
            hint="Артикул продавца — как в личном кабинете Ozon. У заполненных строк не меняйте.",
        ),
        Column(
            "name",
            "Название (справочно)",
            width=50,
            reference=True,
            hint="Название товара в Ozon — для удобства, при загрузке не читается.",
        ),
        Column(
            "sku",
            "SKU (справочно)",
            width=14,
            reference=True,
            hint="SKU товара в Ozon — для удобства, при загрузке не читается.",
        ),
        Column(
            "cost_price",
            COST_TITLE,
            kind="money",
            width=18,
            aliases=("cost_price", "себестоимость", "закупочная цена", "цена закупки", "закупка", "cost", "цена"),
            exclude=("с ндс", "with vat", "with_vat", "ставка", "справочно"),
            hint="Цена закупки одной штуки без НДС — число больше 0, например 1250,50. "
            "Пусто — строка пропускается, текущая цена не меняется.",
        ),
        Column(
            "cost_with_vat",
            "Себестоимость за шт. с НДС, руб.",
            kind="money",
            width=18,
            in_template=False,
            aliases=(
                "cost_with_vat",
                "себестоимость с ндс",
                "закупочная цена с ндс",
                "цена закупки с ндс",
                "цена с ндс",
                "с ндс",
            ),
            exclude=("без ндс", "ставка", "справочно"),
            hint="Вместо цены без НДС: пересчитаем по ставке НДС поставщика.",
        ),
        Column(
            "vat_multiplier",
            "Ставка НДС поставщика",
            kind="rate",
            width=16,
            choices=RATE_CHOICES,
            aliases=(
                "vat_multiplier",
                "ставка ндс",
                "коэффициент ндс",
                "коэф. ндс",
                "коэф ндс",
                "ндс поставщика",
                "vat",
            ),
            exclude=("себестоимость", "цена", "cost", "справочно"),
            hint="Ставка НДС в цене поставщика: 22 %, 10 %, 7 %, 5 % или «без НДС». "
            "Пусто — как в налоговом профиле («Налоги и НДС»).",
        ),
        Column(
            "effective_from",
            "Действует с",
            kind="date",
            width=14,
            aliases=("effective_from", "действует с", "дата начала", "с даты"),
            hint="С какой даты действует цена, ДД.ММ.ГГГГ. Пусто — с сегодня; для товара, у которого цены "
            "ещё не было, — с начала учёта (и для уже загруженных отчётов).",
        ),
        Column(
            "note",
            "Комментарий",
            width=30,
            aliases=("note", "комментарий", "примечание"),
            hint="Необязательно: партия, поставщик, номер счёта.",
        ),
    ),
    required=(("offer_id",), ("cost_price", "cost_with_vat")),
    missing_columns=(
        "Не найдены колонки «Артикул» и «Себестоимость». Скачайте шаблон на странице «Себестоимость» — "
        "в нём уже есть ваши товары — или назовите колонки так же: артикул и себестоимость без НДС; "
        "по желанию — ставка НДС поставщика, действует с, комментарий."
    ),
    instructions=(
        f"1. Заполните колонку «{COST_TITLE}» — цену закупки одной штуки без НДС.",
        "2. Ставка НДС поставщика, «Действует с» и комментарий — по желанию.",
        "3. Сохраните файл и загрузите его на странице «Себестоимость». Сначала покажем, что изменится, — "
        "применится только после подтверждения.",
        "Строки без себестоимости пропускаются, такие же цены остаются как есть. Новая цена — новая запись "
        "в истории: прошлые отчёты считаются по цене, которая действовала в их период.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и не удаляйте строку заголовков — по ним мы находим колонки.",
        "• Excel превращает артикулы вроде 6001 или 1-2 в число или дату. Здесь у колонки артикула формат "
        "«Текст»; копируя артикулы из другого файла, вставляйте их как значения.",
        "• Знаете цену только с НДС? Переименуйте колонку в «Себестоимость за шт. с НДС, руб.» — пересчитаем по "
        "ставке НДС поставщика.",
        "• Формулы можно, но сохраните файл в Excel или Google Таблицах, чтобы в нём были посчитанные значения.",
        "• Пример: артикул 6001-2RS.C3, себестоимость 164,00, ставка 22 %, «Действует с» пусто, "
        "комментарий «партия 5».",
    ),
)

Status = Literal["new", "changed", "unchanged", "skipped", "error"]
RowsKind = Literal["all", "missing", "empty"]
SKIPPED = "Себестоимость не указана — строка пропущена"


class CostImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


@dataclass
class ImportRow:
    line: int  # строка файла (с 1)
    offer_id: str
    cost_price: Decimal | None = None
    cost_with_vat: Decimal | None = None  # если в файле цена с НДС — как указано
    vat_multiplier: Decimal | None = None
    note: str | None = None
    effective_from: date | None = None
    status: Status = "new"
    current_cost: Decimal | None = None
    message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # ячейки как в файле — для файла ошибок


@dataclass
class ParsedFile:
    rows: list[ImportRow]
    format: str  # xlsx | csv
    columns: list[str]  # какие колонки нашлись


def _parse(raw: dict, key: str, parser: Callable[[Any], Any], label: str, errors: list[str]) -> Any:
    if key not in raw:
        return None
    try:
        return parser(raw[key])
    except CellError as exc:
        errors.append(f"{label}: {exc}")
        return None


def _note(value: Any) -> str | None:
    if not is_empty(value) and not isinstance(value, (str, int, float, Decimal, date)):
        raise CellError("формула без сохранённого значения — откройте файл в Excel и сохраните его")
    return cell_text(value)[:255] or None


def parse_cost_file(content: bytes, filename: str, default_vat: Decimal) -> ParsedFile:
    """Строки файла с проверкой каждой. Ошибка файла целиком — CostImportError."""
    try:
        table = read_table(COST_SPEC, content, filename, MAX_ROWS)
    except TemplateError as exc:
        raise CostImportError(str(exc)) from exc
    out: list[ImportRow] = []
    seen: dict[str, int] = {}
    for t in table.rows:
        raw = t.values
        item = ImportRow(line=t.line, offer_id="", raw=raw)
        out.append(item)
        # ошибки — в порядке колонок: артикул, себестоимость, ставка НДС, дата, комментарий
        key_errors: list[str] = []
        cost_errors: list[str] = []
        other_errors: list[str] = []
        try:
            item.offer_id = parse_key(raw.get("offer_id"))
        except CellError as exc:
            key_errors.append(str(exc))
            item.offer_id = cell_text(raw.get("offer_id"))[:100]
        cost = _parse(raw, "cost_price", parse_money, "себестоимость", cost_errors)
        with_vat = _parse(raw, "cost_with_vat", parse_money, "себестоимость с НДС", cost_errors)
        item.vat_multiplier = _parse(raw, "vat_multiplier", parse_rate, "ставка НДС", other_errors)
        item.effective_from = _parse(raw, "effective_from", parse_day, "«Действует с»", other_errors)
        item.note = _parse(raw, "note", _note, "комментарий", other_errors)
        if cost is None and with_vat is None and not cost_errors:
            item.status, item.message = "skipped", SKIPPED  # пустая себестоимость — не ошибка
            continue
        if not item.offer_id:
            key_errors.append("не указан артикул")
        elif len(item.offer_id) > 100:
            key_errors.append("артикул длиннее 100 символов")
        elif item.offer_id in seen:
            key_errors.append(f"артикул уже есть в строке {seen[item.offer_id]}")
        else:
            seen[item.offer_id] = t.line
        if cost is None and with_vat is not None:
            item.cost_with_vat = with_vat
            cost = (with_vat / (item.vat_multiplier or default_vat)).quantize(Decimal("0.01"))
        if cost is not None:
            if cost <= 0 or (with_vat is not None and with_vat <= 0):
                cost_errors.append("себестоимость должна быть больше 0")
            elif cost > MAX_COST:
                cost_errors.append("себестоимость больше 100 000 000 ₽ — проверьте число")
            else:
                item.cost_price = cost.quantize(Decimal("0.01"))
        if errors := key_errors + cost_errors + other_errors:
            text = "; ".join(errors)
            item.status, item.message = "error", text[:1].upper() + text[1:]
    return ParsedFile(out, table.format, table.columns)


def classify(rows: list[ImportRow], history: CostHistory) -> None:
    """new — цены ещё нет, changed — отличается от действующей, unchanged — такая же."""
    today = today_msk()
    for row in rows:
        if row.status in ("error", "skipped"):
            continue
        # новый артикул без даты получит «с начала учёта» — сравниваем с действующей сегодня
        current = history.record_at(row.offer_id, row.effective_from or today)
        row.current_cost = current.cost_price if current else None
        if current is None:
            row.status = "new"
        elif (
            current.cost_price == row.cost_price
            and (row.vat_multiplier is None or current.vat_multiplier == row.vat_multiplier)
            and (row.note is None or current.note == row.note)
        ):
            row.status = "unchanged"
        else:
            row.status = "changed"


@dataclass
class ImportResult:
    rows: list[ImportRow]
    applied: bool
    recosted_reports: int = 0

    def count(self, status: Status) -> int:
        return sum(r.status == status for r in self.rows)


async def _parse_for_user(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> ParsedFile:
    return parse_cost_file(content, filename, await supplier_vat_multiplier(db, user_id))


async def import_costs(
    db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, apply: bool, source: str = "import"
) -> ImportResult:
    rows = (await _parse_for_user(db, user_id, content, filename)).rows
    history = await load_cost_history(db, user_id)
    classify(rows, history)
    if not apply:
        return ImportResult(rows, applied=False)
    changed: list[str] = []
    periods: list[tuple[date | None, date | None]] = []
    for row in rows:
        if row.status in ("new", "changed"):
            record, _ = await add_cost(
                db,
                user_id,
                row.offer_id,
                row.cost_price,
                row.vat_multiplier,
                row.note,
                row.effective_from,
                source=source,
                history=history,
            )
            changed.append(row.offer_id)
            periods.append(affected_period(history.dates(row.offer_id), record.effective_from))
    await db.flush()
    recosted = 0
    if changed:
        recosted = await recost_reports(db, user_id, changed)
        starts, ends = [a for a, _ in periods], [b for _, b in periods]
        start = None if None in starts else min(starts)
        end = None if None in ends else max(ends)
        await invalidate(db, user_id, start, end, f"Себестоимость из файла: {len(changed)} артикулов")
    await db.commit()
    return ImportResult(rows, applied=True, recosted_reports=recosted)


# ---------- шаблоны ----------

TEMPLATE_NAMES = {"all": "все товары", "missing": "без себестоимости", "empty": "пустой шаблон"}


async def cost_template(db: AsyncSession, user_id: uuid.UUID, rows_kind: RowsKind, fmt: str) -> bytes:
    """Шаблон с товарами пользователя: all — все с текущими ценами (это и выгрузка справочника),
    missing — только без себестоимости, empty — без строк. «Действует с» пустая: новая цена — с сегодня."""
    rows: list[dict[str, Any]] = []
    if rows_kind != "empty":
        history = await load_cost_history(db, user_id)
        today = today_msk()
        items = []
        for info in (await known_articles(db, user_id, history)).values():
            current = history.record_at(info.offer_id, today)
            if rows_kind == "missing" and current is not None:
                continue
            items.append((info, current))
        # как на странице: без себестоимости и самые продаваемые — первыми
        items.sort(key=lambda x: (x[1] is not None, -x[0].report_qty, x[0].offer_id))
        rows = [
            {
                "offer_id": info.offer_id,
                "name": info.name,
                "sku": info.sku,
                "cost_price": Decimal(current.cost_price).quantize(Decimal("0.01")) if current else None,
                "vat_multiplier": rate_text(current.vat_multiplier) if current else None,
                "note": current.note if current else None,
            }
            for info, current in items
        ]
    return build_file(fmt, COST_SPEC, rows)


async def error_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> tuple[bytes, str]:
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: исправил → загрузил снова."""
    parsed = await _parse_for_user(db, user_id, content, filename)
    errors = [r for r in parsed.rows if r.status == "error"]
    if not errors:
        raise CostImportError("В файле нет строк с ошибками.")
    articles = await known_articles(db, user_id, await load_cost_history(db, user_id))
    columns = [ERROR_COLUMN] + [c for c in COST_SPEC.columns if c.in_template or c.key in parsed.columns]
    rows = []
    for r in errors:
        info = articles.get(r.offer_id)
        row = {c.key: echo(c.kind, r.raw.get(c.key)) for c in columns if not c.reference}
        row.update(_error=r.message, name=info.name if info else None, sku=info.sku if info else None)
        rows.append(row)
    return build_file(parsed.format, COST_SPEC, rows, columns), parsed.format
