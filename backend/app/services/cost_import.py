"""Импорт себестоимости из CSV или XLSX: предпросмотр → применение.

Колонки ищутся по заголовкам (правило 4): артикул (offer_id), себестоимость без НДС (cost_price),
коэффициент НДС (vat_multiplier, необязательно), комментарий (note), «действует с»
(effective_from, необязательно — иначе сегодня). Каждая строка проверяется отдельно: ошибки
не мешают применить остальные строки.
"""
import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.xlsx.common import ReportParseError, Workbook, cell, cell_text, norm, parse_date, parse_number
from app.services.costs import CostHistory, add_cost, load_cost_history
from app.services.report_costs import recost_reports
from app.services.sync.finance import today_msk

MAX_ROWS = 20_000
HEADER_SCAN = 20
VAT_MIN, VAT_MAX = Decimal("1"), Decimal("2")

# по приоритету: сначала точные названия, потом общие
COLUMNS: dict[str, tuple[str, ...]] = {
    "offer_id": ("offer_id", "артикул продавца", "артикул", "article"),
    "cost_price": ("cost_price", "себестоимость", "закупочная цена", "цена закупки", "закупка", "cost", "цена"),
    "vat_multiplier": ("vat_multiplier", "коэффициент ндс", "коэф. ндс", "коэф ндс", "vat"),
    "note": ("note", "комментарий", "примечание"),
    "effective_from": ("effective_from", "действует с", "дата начала", "с даты"),
}
REQUIRED = ("offer_id", "cost_price")

Status = Literal["new", "changed", "unchanged", "error"]


class CostImportError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


@dataclass
class ImportRow:
    line: int  # строка файла (с 1)
    offer_id: str
    cost_price: Decimal | None = None
    vat_multiplier: Decimal | None = None
    note: str | None = None
    effective_from: date | None = None
    status: Status = "new"
    current_cost: Decimal | None = None
    message: str | None = None


def _read_rows(content: bytes, filename: str) -> list[list]:
    name = filename.lower()
    if content[:4] == b"\xd0\xcf\x11\xe0" or name.endswith(".xls"):
        raise CostImportError("Файл в старом формате Excel (.xls). Сохраните его как .xlsx или .csv и загрузите снова.")
    if content[:2] == b"PK" or name.endswith(".xlsx"):
        try:
            with Workbook(content) as wb:
                if not wb.sheet_names:
                    raise CostImportError("В файле нет листов.")
                for sheet in wb.sheet_names:  # первый лист, где нашлась таблица
                    rows = [list(r) for r in wb.rows(sheet)]
                    if _header_index(rows) is not None:
                        return rows
                return [list(r) for r in wb.rows(wb.sheet_names[0])]
        except ReportParseError as exc:
            raise CostImportError(str(exc)) from exc
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise CostImportError("Не удалось прочитать файл: нужен CSV в кодировке UTF-8 или Windows-1251, или XLSX.")
    first = text.splitlines()[0] if text else ""
    delimiter = max((";", ",", "\t"), key=first.count)
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _find(header: list[str], aliases: tuple[str, ...], used: set[int]) -> int | None:
    for alias in aliases:  # приоритет — порядок вариантов названия
        for j, text in enumerate(header):
            if j not in used and text and alias in text:
                return j
    return None


def _columns(header_row: list) -> dict[str, int]:
    header = [norm(v) for v in header_row]
    found: dict[str, int] = {}
    for key in ("offer_id", "cost_price", "vat_multiplier", "note", "effective_from"):
        idx = _find(header, COLUMNS[key], set(found.values()))
        if idx is not None:
            found[key] = idx
    return found


def _header_index(rows: list[list]) -> int | None:
    for i, row in enumerate(rows[:HEADER_SCAN]):
        cols = _columns(row)
        if all(k in cols for k in REQUIRED):
            return i
    return None


def parse_cost_file(content: bytes, filename: str) -> list[ImportRow]:
    """Строки файла с проверкой каждой. Ошибка файла целиком — CostImportError."""
    rows = _read_rows(content, filename)
    header_idx = _header_index(rows)
    if header_idx is None:
        raise CostImportError(
            "Не найдены колонки «Артикул» и «Себестоимость». Нужна таблица с заголовками: артикул "
            "(offer_id), себестоимость без НДС (cost_price); по желанию — коэффициент НДС "
            "(vat_multiplier), комментарий (note), действует с (effective_from)."
        )
    cols = _columns(rows[header_idx])
    out: list[ImportRow] = []
    seen: dict[str, int] = {}
    for i in range(header_idx + 1, len(rows)):
        row = rows[i]
        if all(cell_text(v) == "" for v in row):
            continue
        if len(out) >= MAX_ROWS:
            raise CostImportError(f"В файле больше {MAX_ROWS} строк — разбейте его на части.")
        line = i + 1
        item = ImportRow(line=line, offer_id=cell_text(cell(row, cols["offer_id"]))[:100])
        out.append(item)
        errors: list[str] = []
        if not item.offer_id:
            errors.append("не указан артикул")
        elif item.offer_id in seen:
            errors.append(f"артикул уже есть в строке {seen[item.offer_id]}")
        else:
            seen[item.offer_id] = line
        warnings: list[str] = []
        price = parse_number(cell(row, cols["cost_price"]), warnings, "Себестоимость")
        if price is None:
            errors.append("себестоимость не указана или не число")
        elif price <= 0:
            errors.append("себестоимость должна быть больше 0")
        else:
            item.cost_price = price.quantize(Decimal("0.01"))
        if "vat_multiplier" in cols and cell_text(cell(row, cols["vat_multiplier"])):
            vat = parse_number(cell(row, cols["vat_multiplier"]), warnings, "НДС")
            if vat is None or not (VAT_MIN <= vat <= VAT_MAX):
                errors.append("коэффициент НДС — число от 1 до 2, например 1,22")
            else:
                item.vat_multiplier = vat.quantize(Decimal("0.001"))
        if "note" in cols:
            item.note = cell_text(cell(row, cols["note"]))[:255] or None
        if "effective_from" in cols and cell_text(cell(row, cols["effective_from"])):
            item.effective_from = parse_date(cell(row, cols["effective_from"]))
            if item.effective_from is None:
                errors.append("дата «действует с» — в формате ДД.ММ.ГГГГ")
        if errors:
            text = "; ".join(errors)
            item.status, item.message = "error", text[:1].upper() + text[1:]
    return out


def classify(rows: list[ImportRow], history: CostHistory) -> None:
    """new — цены ещё нет, changed — отличается от действующей, unchanged — такая же."""
    today = today_msk()
    for row in rows:
        if row.status == "error":
            continue
        # новый артикул без даты получит «с начала учёта» — сравниваем с действующей сегодня
        current = history.record_at(row.offer_id, row.effective_from or today)
        row.current_cost = current.cost_price if current else None
        if current is None:
            row.status = "new"
        elif current.cost_price == row.cost_price and (
            row.vat_multiplier is None or current.vat_multiplier == row.vat_multiplier
        ) and (row.note is None or current.note == row.note):
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


async def import_costs(
    db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, apply: bool, source: str = "import"
) -> ImportResult:
    rows = parse_cost_file(content, filename)
    history = await load_cost_history(db, user_id)
    classify(rows, history)
    if not apply:
        return ImportResult(rows, applied=False)
    changed: list[str] = []
    for row in rows:
        if row.status in ("new", "changed"):
            await add_cost(
                db, user_id, row.offer_id, row.cost_price, row.vat_multiplier, row.note, row.effective_from,
                source=source, history=history,
            )
            changed.append(row.offer_id)
    await db.flush()
    recosted = await recost_reports(db, user_id, changed) if changed else 0
    await db.commit()
    return ImportResult(rows, applied=True, recosted_reports=recosted)
