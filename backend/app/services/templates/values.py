"""Значения ячеек шаблона: разбор того, что ввёл пользователь (или во что это превратил Excel),
и запись наших значений в XLSX / CSV.

Ошибка значения — CellError с текстом для пользователя: что не так и как исправить.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl.utils.datetime import from_excel

from app.integrations.xlsx.common import cell_text, norm, parse_date, parse_number


class CellError(Exception):
    """Значение ячейки не подходит. Текст — для пользователя, со строчной буквы."""


class _Formula:
    def __repr__(self) -> str:
        return "FORMULA"


FORMULA = _Formula()  # в ячейке формула, а посчитанного значения в файле нет
FORMULA_ERROR = "формула без сохранённого значения — откройте файл в Excel и сохраните его"

# ставки НДС в РФ, %: действующие и прежние (для цен с прошлыми датами)
VAT_RATES = (22, 20, 18, 10, 7, 5, 0)
RATE_CHOICES = ("22 %", "20 %", "10 %", "7 %", "5 %", "0 %", "без НДС")
RATE_ERROR = "нужно 22 %, 20 %, 10 %, 7 %, 5 %, 0 % или «без НДС» (можно и 1,22)"
_NO_VAT = ("без", "не облага", "нет")

_SCIENTIFIC = re.compile(r"^\d+(?:[.,]\d+)?e[+-]?\d+$", re.IGNORECASE)
_MONTHS = (
    r"(?:янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"[a-zа-я]*\.?"
)
# так Excel показывает дату, в которую превратил «1-2» или «3/4», и так сохраняет её в CSV: «02.янв», «2-Jan»
_MANGLED_DATE = re.compile(rf"^(?:\d{{1,2}}[.\- ]{_MONTHS}|{_MONTHS}[.\- ]\d{{1,4}})$", re.IGNORECASE)
_EXCEL_DAYS = (30_000, 80_000)  # даты Excel 1982–2119, записанные числом


def _check(value: Any) -> None:
    if value is FORMULA:
        raise CellError(FORMULA_ERROR)


def is_empty(value: Any) -> bool:
    return value is not FORMULA and cell_text(value) == ""


def parse_key(value: Any, what: str = "артикул") -> str:
    """Текстовый ключ (артикул), даже если Excel превратил его в число или дату."""
    _check(value)
    if isinstance(value, (datetime, date)):
        raise CellError(
            f"Excel превратил {what} в дату ({cell_text(value)}) — задайте колонке формат «Текст» "
            f"и введите {what} заново"
        )
    if isinstance(value, float) and not value.is_integer():
        return format(Decimal(repr(value)).normalize(), "f")
    text = cell_text(value)
    if _MANGLED_DATE.fullmatch(text):
        raise CellError(
            f"похоже, Excel превратил {what} в дату («{text}») — задайте колонке формат «Текст» и введите {what} заново"
        )
    if _SCIENTIFIC.fullmatch(text.replace(" ", "")):
        raise CellError(
            f"Excel сократил длинный {what} до «{text}» — задайте колонке формат «Текст» и введите {what} заново"
        )
    return text


def parse_money(value: Any) -> Decimal | None:
    """Сумма: «1 250,50», «1250.5 ₽», число Excel. Пусто → None."""
    _check(value)
    if isinstance(value, (datetime, date)):
        raise CellError("вместо суммы дата — проверьте колонку")
    unreadable: list[str] = []
    number = parse_number(value, unreadable)
    if unreadable:
        raise CellError(f"«{cell_text(value)[:40]}» — не число")
    return number


def parse_rate(value: Any) -> Decimal | None:
    """Ставка НДС → коэффициент: «22 %», «22», «0,22», «1,22» → 1.220; «без НДС», «0» → 1.000. Пусто → None."""
    _check(value)
    text = norm(value)
    if not text:
        return None
    if any(word in text for word in _NO_VAT):
        return Decimal("1.000")
    number = parse_number(text.replace("%", "")) if isinstance(value, str) else parse_number(value)
    if number is None or isinstance(value, (datetime, date)):
        raise CellError(RATE_ERROR)
    if "%" in text or number >= 2 or number == 0:
        pct = number
    elif number < 1:
        pct = number * 100  # доля: 0,22 (в том числе ячейка Excel в формате «процент»)
    else:
        pct = (number - 1) * 100  # коэффициент: 1,22
    if pct not in VAT_RATES:
        raise CellError(RATE_ERROR)
    return (1 + pct / 100).quantize(Decimal("0.001"))


def rate_text(multiplier: Decimal | None) -> str:
    """Коэффициент → как в шаблоне: 1.220 → «22 %», 1.000 → «без НДС», None → пусто."""
    if multiplier is None:
        return ""
    pct = (Decimal(multiplier) - 1) * 100
    return "без НДС" if pct == 0 else f"{pct.normalize():f} %"


def parse_day(value: Any) -> date | None:
    """Дата: дата Excel, «01.09.2026», «2026-09-01», дата Excel числом. Пусто → None."""
    _check(value)
    if is_empty(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and _EXCEL_DAYS[0] <= value <= _EXCEL_DAYS[1]:
        return from_excel(value).date()
    day = parse_date(value)
    if day is None:
        raise CellError("нужна дата ДД.ММ.ГГГГ, например 01.09.2026")
    return day


def echo(kind: str, value: Any) -> Any:
    """Значение из загруженного файла — обратно в файл ошибок, как его ввёл пользователь."""
    if value is FORMULA or value is None:
        return None
    if kind in ("money", "date") and not isinstance(value, str):
        return value
    return cell_text(value)


def csv_text(kind: str, value: Any) -> str:
    """Значение для CSV: суммы с запятой, даты ДД.ММ.ГГГГ (так их понимает русский Excel)."""
    if value is None:
        return ""
    if kind == "money" and isinstance(value, (Decimal, int, float)):
        return f"{Decimal(value).quantize(Decimal('0.01')):f}".replace(".", ",")
    if isinstance(value, (datetime, date)):
        return value.strftime("%d.%m.%Y")
    return cell_text(value)
