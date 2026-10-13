"""Разбор XLSX-отчётов Ozon: определить тип отчёта по содержимому и привести к NormalizedReportData."""
from app.integrations.xlsx.common import ReportParseError, Workbook
from app.integrations.xlsx.costs import CostProvider, MappingCostProvider  # noqa: F401
from app.integrations.xlsx.detector import MAX_SCAN, detect
from app.integrations.xlsx.parsers import parse_accrual, parse_realization
from app.schemas.report import NormalizedReportData

SUPPORTED = (
    "«Отчёт по начислениям» (Финансы → Отчёт по начислениям) и "
    "«Отчёт о реализации» (Финансы → Отчёт о реализации)"
)
_PARSERS = {"accrual": parse_accrual, "realization": parse_realization}


def parse_report(content: bytes, costs: CostProvider, filename: str | None = None) -> NormalizedReportData:
    """Разобрать файл отчёта. Ошибки — ReportParseError с текстом для пользователя."""
    with Workbook(content) as wb:
        if not wb.sheet_names:
            raise ReportParseError("В файле нет ни одного листа.")
        detection = detect({name: wb.rows(name, limit=MAX_SCAN) for name in wb.sheet_names}, filename)
        if detection is None:
            raise ReportParseError(
                f"Не удалось определить тип отчёта Ozon. Поддерживаются {SUPPORTED}. "
                f"Листы в файле: {', '.join(wb.sheet_names)}."
            )
        rows = wb.rows(detection.sheet_name)
    return _PARSERS[detection.report_type](rows, detection, costs)
