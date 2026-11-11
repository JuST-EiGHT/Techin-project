"""Отчёт Ozon «Стоимость размещения по товарам» (шаг 4.2a): `/v1/report/placement/by-products/create` → код →
`/v1/report/info` → файл. Тот же отчёт, что в кабинете: FBO → Стоимость размещения.

Колонок отчёта в документации Ozon нет — таблицу ищем по заголовкам (правило 4), с запасом на разные названия:
- товар — «SKU» и/или «Артикул» (нужно хотя бы одно);
- сумма — «Стоимость размещения» (или «Сумма …», «Начислено», «Итого» — по убыванию уверенности); колонки объёма,
  количества и тарифа суммой не считаем;
- день — «Дата» / «День», если есть: тогда строки по дням; нет — сумма за весь период отчёта.
Файл — XLSX (лист с такой таблицей) или CSV («;», «,» или табуляция; UTF-8 или Windows-1251). Строки «Итого» и пустые
пропускаем. Знак суммы не важен: это вес доли товара в хранении магазина за день.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.integrations.xlsx.common import ReportParseError, Row, Workbook, cell_text, norm, parse_date, parse_number

HEADER_SCAN = 30  # строк сверху, где ищем шапку таблицы
NOT_AMOUNT = ("объем", "литр", "количеств", "шт", "тариф", "ставк", "дней", "срок", "%")
TOTAL_WORDS = ("итого", "всего")


@dataclass(frozen=True)
class PlacementRow:
    day: date | None
    sku: int | None
    offer_id: str | None
    amount: Decimal


@dataclass(frozen=True)
class Columns:
    sku: int | None
    offer: int | None
    amount: int
    day: int | None


def _amount_column(headers: list[str]) -> int | None:
    """Колонка суммы — по убыванию уверенности; объём, количество, тариф — не сумма."""
    candidates = [(i, h) for i, h in enumerate(headers) if h and not any(w in h for w in NOT_AMOUNT)]
    tiers = (
        lambda h: "стоимост" in h and "размещ" in h,
        lambda h: "сумм" in h and ("размещ" in h or "начисл" in h or "итог" in h),
        lambda h: "стоимост" in h and "товар" not in h,
        lambda h: "сумм" in h,
        lambda h: "начислен" in h,
        lambda h: "итого" in h,
    )
    for tier in tiers:
        for i, h in candidates:
            if tier(h):
                return i
    return None


def find_columns(row: Row) -> Columns | None:
    headers = [norm(v) for v in row]
    sku = next((i for i, h in enumerate(headers) if h == "sku" or h.startswith("sku") or h.endswith(" sku")), None)
    offer = next((i for i, h in enumerate(headers) if "артикул" in h and "ozon" not in h), None)
    if offer is None:
        offer = next((i for i, h in enumerate(headers) if "артикул" in h), None)
    if sku is None and offer is None:
        return None
    amount = _amount_column(headers)
    if amount is None or amount in (sku, offer):
        return None
    day = next((i for i, h in enumerate(headers) if h.startswith("дата") or h in ("день", "date")), None)
    return Columns(sku, offer, amount, day)


def _rows_from_csv(content: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ReportParseError("Не удалось прочитать файл отчёта о размещении: неизвестная кодировка.")
    sample = text[:4096]
    delimiter = max((";", ",", "\t"), key=sample.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def _tables(content: bytes) -> list[list[Row]]:
    if content[:2] == b"PK":  # XLSX — zip
        with Workbook(content) as wb:
            return [wb.rows(name) for name in wb.sheet_names]
    return [_rows_from_csv(content)]


def _sku(value) -> int | None:
    text = cell_text(value).strip()
    return int(text) if text.isdigit() else None


def parse(content: bytes) -> list[PlacementRow]:
    """Строки отчёта: день (или None), SKU, артикул, сумма. Не нашли таблицу или в ней нет сумм — ReportParseError."""
    found_header = False
    for rows in _tables(content):
        for n, row in enumerate(rows[:HEADER_SCAN]):
            cols = find_columns(row)
            if cols is None:
                continue
            found_header = True
            out: list[PlacementRow] = []
            for r in rows[n + 1:]:
                first = norm(next((v for v in r if cell_text(v)), ""))
                if not first or any(first.startswith(w) for w in TOTAL_WORDS):
                    continue
                cell = lambda i: r[i] if i is not None and i < len(r) else None  # noqa: E731
                amount = parse_number(cell(cols.amount))
                sku, offer = _sku(cell(cols.sku)), cell_text(cell(cols.offer)).strip() or None
                if amount is None or amount == 0 or (sku is None and offer is None):
                    continue
                day = parse_date(cell(cols.day)) if cols.day is not None else None
                out.append(PlacementRow(day, sku, offer[:100] if offer else None, abs(amount)))
            if out:
                return out
    if found_header:
        raise ReportParseError("В отчёте о размещении нет строк с суммами по товарам.")
    raise ReportParseError(
        "Не нашли в отчёте о размещении таблицу с колонками «SKU» или «Артикул» и «Стоимость размещения» — Ozon мог "
        "изменить отчёт."
    )
