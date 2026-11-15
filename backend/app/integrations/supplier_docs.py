"""Документы поставщика (шаг 10.23a): сборочный лист / отгрузка в PDF → поставка со строками «артикул × количество».

Разбор семантический (правило 4): таблица — та, где в строке заголовков есть «Артикул» и «Количество»; колонки — по
заголовкам, а не по номерам; шапка документа — по подписям («Организация:», «Контрагент:», «Склад:») и строке
«… № 587 от 10.11.2026». Пример формата — сборочный лист поставщика (`tests/fixtures/supplier_pick_list.pdf`):

| В сервисе          | Где в документе                                                           |
|--------------------|---------------------------------------------------------------------------|
| № и дата поставки  | заголовок «Сборочный лист – Отгрузка № 587 от 10.11.2026»                 |
| поставщик          | «Организация:»                                                             |
| получатель, склад  | «Контрагент:», «Склад:» — только показываем                                |
| артикул            | колонка «Артикул» — единственный ключ сопоставления с товаром и с Ozon     |
| количество         | «Количество»; «Единица измерения» — только штуки                           |
| сверка             | «Общее количество» = сумма строк                                           |
| не используем      | «Ячейка», «Наименование» (подпись), «Штрихкод» (бывает пустым)            |

Цены в документе нет — её берёт сервис из справочника себестоимости. Скан без текста не читается — понятная ошибка.
"""
import io
import re
from dataclasses import dataclass, field
from datetime import date

from app.integrations.xlsx.common import cell_text, norm

MAX_PAGES = 50
UNITS_OK = ("шт", "шт.", "штука", "штуки", "штук", "pcs", "pc")
# тип колонки → части заголовка; порядок важен: «Штрихкод товара» — штрихкод, а не наименование
COLUMNS = (
    ("barcode", ("штрихкод", "штрих-код", "штрих код", "ean")),
    ("article", ("артикул",)),
    ("qty", ("количеств", "кол-во", "кол во")),
    ("unit", ("единиц", "ед. изм", "ед изм")),
    ("name", ("наименован", "товар", "номенклатур")),
)
TITLE = re.compile(r"№\s*([^\s,]+)\s+от\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})", re.IGNORECASE)
LABELS = {
    "supplier": ("организация", "поставщик", "продавец", "грузоотправитель"),
    "receiver": ("контрагент", "покупатель", "получатель", "грузополучатель"),
    "warehouse": ("склад",),
}
TOTAL = re.compile(r"(?:общее количество|итого|всего)\D{0,20}?(\d[\d\s]*)", re.IGNORECASE)
NO_TABLE = (
    "В документе не нашли таблицу с колонками «Артикул» и «Количество». Загрузите сборочный лист или накладную "
    "поставщика в PDF (не скан) — или заполните шаблон «Поставки списком»."
)


class DocError(Exception):
    """Документ целиком не подходит. Текст — для пользователя."""


@dataclass
class DocLine:
    line: int  # строка таблицы документа, с 1
    article: str
    qty: int | None
    name: str | None = None
    barcode: str | None = None
    sold_before: int = 0
    error: str | None = None


@dataclass
class Doc:
    number: str | None
    day: date | None
    supplier: str | None = None
    receiver: str | None = None
    warehouse: str | None = None
    total_qty: int | None = None
    lines: list[DocLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # ошибки документа целиком (нет № или даты)
    source: str = "pdf"

    @property
    def lines_qty(self) -> int:
        return sum(ln.qty or 0 for ln in self.lines if ln.error is None)


def parse_date(d: str, m: str, y: str) -> date | None:
    year = int(y) + (2000 if len(y) == 2 else 0)
    try:
        return date(year, int(m), int(d))
    except ValueError:
        return None


def parse_qty(value, allow_zero: bool = False) -> int:
    """«17», «17,000», «17 шт» → 17. Дробное или пустое — ошибка; ноль — только если allow_zero."""
    text = cell_text(value).lower().replace("\xa0", " ").replace(" ", "")
    for unit in UNITS_OK:
        if text.endswith(unit):
            text = text[: -len(unit)]
            break
    text = text.replace(",", ".")
    if not text:
        raise ValueError("не указано количество")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"количество «{cell_text(value)}» — не число") from exc
    if number != int(number) or number < 0 or (number == 0 and not allow_zero):
        what = "от нуля" if allow_zero else "больше нуля"
        raise ValueError(f"«{cell_text(value)}» — нужно целое число штук {what}")
    return int(number)


def _columns(header: list) -> dict[str, int] | None:
    found: dict[str, int] = {}
    for idx, value in enumerate(header):
        text = norm(value)
        if not text:
            continue
        for key, parts in COLUMNS:
            if key not in found and any(p in text for p in parts):
                found[key] = idx
                break
    return found if "article" in found and "qty" in found else None


def _head(text: str, doc: Doc) -> None:
    if (m := TITLE.search(text)) is not None:
        doc.number = m.group(1).strip()
        doc.day = parse_date(m.group(2), m.group(3), m.group(4))
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = norm(label)
        for key, names in LABELS.items():
            if label in names and getattr(doc, key) is None and value.strip():
                setattr(doc, key, " ".join(value.split())[:300])
    totals = TOTAL.findall(text)
    if totals:
        doc.total_qty = int(totals[-1].replace(" ", ""))


def _rows(doc: Doc, tables: list[list[list]]) -> None:
    cols: dict[str, int] | None = None
    n = 0
    for table in tables:
        for row in table:
            if not row or all(not cell_text(c).strip() for c in row):
                continue
            found = _columns(row)
            if found is not None:  # строка заголовков (повторяется на каждой странице)
                cols = found
                continue
            if cols is None:
                continue
            first = norm(" ".join(cell_text(c) for c in row if c))
            if first.startswith(("итого", "общее количество", "всего")):
                continue

            def get(key: str, cells=row, mapping=cols) -> str:
                i = mapping.get(key)
                return " ".join(cell_text(cells[i]).split()) if i is not None and i < len(cells) else ""

            n += 1
            article = get("article")
            ln = DocLine(line=n, article=article[:100], qty=None, name=get("name")[:300] or None,
                         barcode=get("barcode")[:64] or None)
            doc.lines.append(ln)
            if not article:
                ln.error = "нет артикула — строку не к чему отнести"
                continue
            unit = norm(get("unit"))
            if unit and unit not in UNITS_OK:
                ln.error = f"единица «{get('unit')}» — сервис считает поставку в штуках"
                continue
            try:
                ln.qty = parse_qty(get("qty"))
            except ValueError as exc:
                ln.error = str(exc)
    if cols is None:
        raise DocError(NO_TABLE)


def check(doc: Doc) -> Doc:
    if not doc.number:
        doc.errors.append("Не нашли номер документа («… № 587 от 10.11.2026») — укажите его в шаблоне")
    if doc.day is None:
        doc.errors.append("Не нашли дату документа («… № 587 от 10.11.2026») — укажите её в шаблоне")
    if not doc.lines:
        doc.errors.append("В документе нет строк с товарами")
    if doc.total_qty is not None and not any(ln.error for ln in doc.lines) and doc.total_qty != doc.lines_qty:
        doc.warnings.append(
            f"В документе «Общее количество» {doc.total_qty}, а по строкам — {doc.lines_qty}: проверьте, все ли строки "
            "прочитались."
        )
    return doc


def parse_pdf(content: bytes) -> Doc:
    import pdfplumber  # тяжёлый импорт — только когда загружают PDF

    try:
        pdf = pdfplumber.open(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 — любой битый файл: понятная ошибка
        raise DocError("Не удалось открыть PDF — файл повреждён или это не PDF") from exc
    with pdf:
        if len(pdf.pages) > MAX_PAGES:
            raise DocError(f"В документе больше {MAX_PAGES} страниц — это не похоже на документ поставки")
        text_parts, tables = [], []
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
            tables.extend(page.extract_tables() or [])
    text = "\n".join(text_parts)
    if not text.strip():
        raise DocError("В PDF нет текста — похоже, это скан. Нужен документ, выгруженный из программы поставщика, "
                       "или шаблон «Поставки списком».")
    doc = Doc(number=None, day=None, source="pdf")
    _head(text, doc)
    _rows(doc, tables)
    return check(doc)

