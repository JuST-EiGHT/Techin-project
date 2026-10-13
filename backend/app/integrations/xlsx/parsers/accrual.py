"""«Отчёт по начислениям» Ozon (Финансы → Отчёт по начислениям).

Одна плоская таблица: каждая строка — одна операция («Выручка», «Логистика», «Баллы за скидки» …).
  auto_total   — сумма колонки «Сумма итого» по всем операциям (строкам с типом начисления; строки
                 без типа — ручные пометки под таблицей — не учитываются, о них предупреждение);
  revenue_base — сумма строк «Выручка»;
  cost_sales   — себестоимость × количество, только по строкам «Выручка».
"""
from collections.abc import Sequence
from decimal import Decimal

from app.integrations.xlsx.common import (
    ReportParseError,
    Row,
    cell,
    cell_text,
    extract_period,
    is_empty_row,
    limit_warnings,
    norm,
    parse_number,
    parse_qty,
)
from app.integrations.xlsx.costs import CostProvider
from app.integrations.xlsx.detector import Detection
from app.integrations.xlsx.parsers.base import SalesLedger, money
from app.schemas.report import NormalizedReportData

COL_ALIASES: dict[str, tuple[str, ...]] = {
    "type": ("тип начислен",),
    "sum": ("сумма итого", "итого, руб", "сумма, руб"),
    "art": ("артикул",),
    "sku": ("sku", "скю"),
    "name": ("название товара", "наименование товара", "название", "наименование"),
    "qty": ("количество", "кол-во", "кол во"),
}
REQUIRED = {"type": "Тип начисления", "sum": "Сумма итого, руб."}
REVENUE_TYPE = "выручка"


def find_column(header: Row, aliases: Sequence[str]) -> int | None:
    """Первая колонка, заголовок которой содержит любой из вариантов названия."""
    for j, value in enumerate(header):
        text = norm(value)
        if text and any(a in text for a in aliases):
            return j
    return None


def column_map(header: Row) -> dict[str, int]:
    found = {name: find_column(header, aliases) for name, aliases in COL_ALIASES.items()}
    return {name: idx for name, idx in found.items() if idx is not None}


def parse_accrual(rows: Sequence[Row], detection: Detection, costs: CostProvider) -> NormalizedReportData:
    header_idx = detection.header_idx
    cols = column_map(rows[header_idx])
    for key, title in REQUIRED.items():
        if key not in cols:
            raise ReportParseError(
                f"Не найдена обязательная колонка «{title}» в отчёте по начислениям "
                f"(лист «{detection.sheet_name}», строка заголовков {header_idx + 1}). "
                "Проверьте, что загружен «Отчёт по начислениям» Ozon."
            )
    period, start, end = extract_period(rows[:header_idx])

    warnings: list[str] = []
    ledger = SalesLedger(costs)
    auto_total = Decimal(0)
    revenue_base = Decimal(0)
    by_type: dict[str, Decimal] = {}
    untyped_rows, untyped_total = 0, Decimal(0)

    for i in range(header_idx + 1, len(rows)):
        row = rows[i]
        if is_empty_row(row):
            continue
        where = f" (строка {i + 1})"
        type_raw = cell_text(cell(row, cols["type"]))
        if not type_raw:
            # у каждой операции Ozon есть тип; строка без типа — итоги или пометки, дописанные вручную
            untyped = parse_number(cell(row, cols["sum"]))
            if untyped is not None:
                untyped_rows += 1
                untyped_total += untyped
            continue
        amount = parse_number(cell(row, cols["sum"]), warnings, "Сумма итого", where) or Decimal(0)
        auto_total += amount
        by_type[type_raw] = by_type.get(type_raw, Decimal(0)) + amount
        if norm(type_raw) != REVENUE_TYPE:
            continue
        revenue_base += amount
        ledger.add(
            article=cell_text(cell(row, cols.get("art"))),
            sku=cell_text(cell(row, cols.get("sku"))),
            name=cell_text(cell(row, cols.get("name"))),
            qty=parse_qty(cell(row, cols.get("qty")), warnings, where),
            revenue=amount,
        )

    if not by_type:
        raise ReportParseError(
            f"Таблица найдена, но строк с данными нет (лист «{detection.sheet_name}», строка заголовков "
            f"{header_idx + 1}). Проверьте, что в отчёте есть данные за выбранный период."
        )
    if untyped_rows:
        warnings.append(
            f"Строк без «Типа начисления» не учтено: {untyped_rows} (на сумму {money(untyped_total)} ₽). "
            "Это не операции Ozon — похоже на итоги или пометки, добавленные в файл вручную."
        )
    if ledger.rows == 0:
        warnings.append("В отчёте нет строк с типом «Выручка» — продаж за период нет, маржу посчитать нельзя.")
    warnings.extend(ledger.warnings("«Выручка»"))

    return NormalizedReportData(
        report_type="accrual",
        period=period,
        period_start=start,
        period_end=end,
        auto_total=money(auto_total),
        revenue_base=money(revenue_base),
        cost_sales=money(ledger.cost_sales),
        by_type={k: money(v) for k, v in by_type.items()},
        by_article=ledger.by_article(),
        missing_cost=ledger.missing_cost(),
        warnings=limit_warnings(warnings),
        sheet_name=detection.sheet_name,
        header_row=header_idx + 1,
        rows_count=ledger.rows,
    )
