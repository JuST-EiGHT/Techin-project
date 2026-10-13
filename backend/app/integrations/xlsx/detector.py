"""Определение типа отчёта Ozon по содержимому (как ReportDetector в reference/ozon_calculator.html).

На каждом листе просматриваются первые 60 строк. Строка-кандидат в заголовки таблицы должна
содержать все обязательные признаки типа и ни одного стоп-слова; бонусные признаки (характерные
колонки) повышают уверенность. Побеждает кандидат с наибольшей уверенностью, при равенстве —
более ранняя строка. Имя файла — только дополнительный сигнал при равенстве.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.integrations.xlsx.common import norm
from app.schemas.report import ReportType

MAX_SCAN = 60

Check = Callable[[list[str]], bool]


def _has(*parts: str) -> Check:
    return lambda cells: any(p in c for c in cells for p in parts)


def _starts(*parts: str) -> Check:
    return lambda cells: any(c.startswith(p) for c in cells for p in parts)


@dataclass(frozen=True)
class Signals:
    required: tuple[Check, ...]
    bonus: tuple[Check, ...]
    exclude: tuple[Check, ...] = ()


SIGNALS: dict[ReportType, Signals] = {
    "accrual": Signals(
        required=(_has("тип начислен"), _has("сумма итого", "итого, руб", "сумма, руб")),
        bonus=(_has("артикул"), _has("sku", "скю"), _has("вознаграждение"), _has("логистик"), _has("количество")),
    ),
    "realization": Signals(
        # именно заголовок «Реализовано», а не «Итого реализовано» из строк итогов
        required=(_starts("реализован"), _starts("артикул")),
        bonus=(
            _has("кол-во", "количество"),
            _has("штрих-код", "баркод"),
            _has("sku", "скю"),
            _has("название товара", "наименование"),
            lambda cells: any(c.startswith("№") or "п/п" in c for c in cells),
        ),
        # «тип начисления» — это отчёт по начислениям; «Показной отчёт о реализации» — другой отчёт
        exclude=(_has("тип начислен"), _has("показной")),
    ),
}

# имя файла: только при равной уверенности
_FILENAME_HINTS: dict[ReportType, tuple[str, ...]] = {
    "accrual": ("начислен", "accrual"),
    "realization": ("реализац", "realization"),
}


@dataclass(frozen=True)
class Detection:
    report_type: ReportType
    sheet_name: str
    header_idx: int  # индекс строки заголовков (с 0)
    confidence: int


def detect(sheets: Mapping[str, Sequence[Sequence[Any]]], filename: str | None = None) -> Detection | None:
    """sheets — имя листа → первые строки листа (достаточно MAX_SCAN)."""
    fname = (filename or "").lower()
    candidates: list[tuple[int, int, int, Detection]] = []
    for sheet_name, rows in sheets.items():
        above: list[str] = []  # всё, что выше строки: «показной» обычно в названии отчёта
        for i, row in enumerate(rows[:MAX_SCAN]):
            cells = [c for c in (norm(v) for v in row) if c]
            for report_type, sig in SIGNALS.items():
                if not all(check(cells) for check in sig.required):
                    continue
                if any(check(cells) or check(above) for check in sig.exclude):
                    continue
                bonus = sum(check(cells) for check in sig.bonus)
                confidence = 100 + bonus * 10 + max(0, 5 - i)
                hint = int(any(h in fname for h in _FILENAME_HINTS[report_type]))
                candidates.append((confidence, hint, -i, Detection(report_type, sheet_name, i, confidence)))
            above.extend(cells)
    if not candidates:
        return None
    # сортировка устойчивая: при полном равенстве побеждает более ранний лист
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    return candidates[0][3]
