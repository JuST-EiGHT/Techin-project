"""«Отчёт о реализации» Ozon (Финансы → Отчёт о реализации), месячный.

Над таблицей — шапка с реквизитами и периодом. Заголовок таблицы в две строки: главная
(«Артикул», «Реализовано» …) и подзаголовок («Реализовано на сумму, руб.», «Кол-во» …) — колонки
ищутся в обеих. Дальше строка с номерами столбцов, строки товаров (№ п/п > 0) и итоги, где значение
лежит правее подписи.
  auto_total   — «Итого реализовано (за вычетом возвратов)» + «Всего выплат от партнёров»;
  revenue_base — «Итого реализовано»;
  cost_sales   — себестоимость × количество по всем строкам товаров.
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
    is_numbering_row,
    limit_warnings,
    norm,
    parse_number,
    parse_qty,
    parse_row_number,
)
from app.integrations.xlsx.costs import CostProvider
from app.integrations.xlsx.detector import Detection
from app.integrations.xlsx.parsers.base import ParsedReport, SalesLedger, money
from app.schemas.report import NormalizedReportData

# порядок важен: колонка достаётся первому подходящему названию, сначала в главной строке заголовка
COL_ALIASES: dict[str, tuple[str, ...]] = {
    "num": ("№ п/п", "№", "номер"),
    "name": ("название товара", "наименование товара", "название", "наименование"),
    "art": ("артикул",),
    "sku": ("sku", "скю"),
    "bar": ("штрих-код",),
    "rev": ("реализовано на сумму", "реализован", "на сумму, руб"),
    "pay": ("выплаты по механикам", "выплаты"),
    "qty": ("кол-во", "количество", "кол во"),
}

REALIZED_TITLE = "Итого реализовано (за вычетом возвратов)"
PAYMENTS_TITLE = "Всего выплат от партнёров"
SUMMARY_PATTERNS = {
    "realized": ("итого реализовано", "итого реализован"),
    "payments": ("всего выплат от партнеров", "всего выплат", "выплат от партнеров"),
}


def column_map(rows: Sequence[Row], header_idx: int) -> dict[str, int]:
    header_rows = [rows[i] for i in (header_idx, header_idx + 1) if i < len(rows)]
    found: dict[str, int] = {}
    for name, aliases in COL_ALIASES.items():
        wanted = [norm(a) for a in aliases]
        for row in header_rows:
            idx = next((j for j, v in enumerate(row) if (t := norm(v)) and any(a in t for a in wanted)), None)
            if idx is not None:
                found[name] = idx
                break
    return found


def _is_data_row(row: Row, num_col: int) -> bool:
    return parse_row_number(cell(row, num_col)) is not None and not is_numbering_row(row)


def find_data_start(rows: Sequence[Row], header_idx: int, num_col: int) -> int:
    for i in range(header_idx + 1, min(len(rows), header_idx + 6)):
        if _is_data_row(rows[i], num_col):
            return i
    return header_idx + 3  # заголовок + подзаголовок + номера столбцов


def find_summary(rows: Sequence[Row]) -> dict[str, Decimal | None]:
    """Итоги отчёта: значение — первая непустая ячейка правее подписи, которую удалось прочитать как число."""
    found: dict[str, Decimal | None] = {key: None for key in SUMMARY_PATTERNS}
    for row in rows:
        for j, value in enumerate(row):
            text = norm(value)
            if not text:
                continue
            for key, patterns in SUMMARY_PATTERNS.items():
                if not any(p in text for p in patterns):
                    continue
                right = next((v for v in row[j + 1:] if cell_text(v)), None)
                number = parse_number(right)
                if number is not None:
                    found[key] = number  # как в эталоне: побеждает последняя найденная подпись
    return found


def parse_realization(rows: Sequence[Row], detection: Detection, costs: CostProvider) -> ParsedReport:
    header_idx = detection.header_idx
    cols = column_map(rows, header_idx)
    if "art" not in cols:
        raise ReportParseError(
            f"В отчёте о реализации не найдена колонка «Артикул» (лист «{detection.sheet_name}», "
            f"строка заголовков {header_idx + 1}). Проверьте, что загружен «Отчёт о реализации» Ozon."
        )
    period, start, end = extract_period(rows[:header_idx])

    warnings: list[str] = []
    summary = find_summary(rows)
    realized, payments = summary["realized"], summary["payments"]
    if realized is None:
        warnings.append(
            f"В отчёте о реализации не найден показатель «{REALIZED_TITLE}» — используется сумма строк "
            "таблицы (без учёта возвратов). Проверьте файл."
        )
    if payments is None:
        warnings.append(
            f"В отчёте о реализации не найден показатель «{PAYMENTS_TITLE}» — принят равным 0. Проверьте файл."
        )

    ledger = SalesLedger(costs)
    num_col = cols.get("num", 0)
    for i in range(find_data_start(rows, header_idx, num_col), len(rows)):
        row = rows[i]
        if is_empty_row(row) or not _is_data_row(row, num_col):
            continue
        where = f" (строка {i + 1})"
        ledger.add(
            article=cell_text(cell(row, cols["art"])),
            sku=cell_text(cell(row, cols.get("sku"))),
            name=cell_text(cell(row, cols.get("name"))),
            qty=parse_qty(cell(row, cols.get("qty")), warnings, where),
            revenue=parse_number(cell(row, cols.get("rev")), warnings, "Реализовано на сумму", where) or Decimal(0),
        )

    if realized is None:
        realized = ledger.revenue
    payments = payments if payments is not None else Decimal(0)
    by_type = {
        title: money(value) for title, value in ((REALIZED_TITLE, realized), (PAYMENTS_TITLE, payments)) if value
    }
    if not by_type and ledger.rows == 0:
        raise ReportParseError(
            f"Таблица найдена, но строк с данными нет (лист «{detection.sheet_name}», строка заголовков "
            f"{header_idx + 1}). Проверьте, что в отчёте есть данные за выбранный период."
        )
    warnings.extend(ledger.warnings("таблицы"))

    return ParsedReport(NormalizedReportData(
        report_type="realization",
        period=period,
        period_start=start,
        period_end=end,
        auto_total=money(realized + payments),
        revenue_base=money(realized),
        cost_sales=money(ledger.cost_sales),
        by_type=by_type,
        by_article=ledger.by_article(),
        missing_cost=ledger.missing_cost(),
        warnings=limit_warnings(warnings),
        sheet_name=detection.sheet_name,
        header_row=header_idx + 1,
        rows_count=ledger.rows,
    ))
