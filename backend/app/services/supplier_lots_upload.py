"""Загрузка поставок (шаг 10.23a): документ поставщика PDF или шаблон «Поставки списком» (XLSX / CSV, правило 14).

Сначала предпросмотр — что в документе, какие артикулы нашлись в каталоге, цена из справочника, сумма, ошибки по
строкам; применяется после подтверждения. Та же поставка (поставщик, № и дата) загружена снова: состав тот же —
«без изменений», другой — новая версия (правило 12). Документ сохраняется как есть.

«Уже продано ранее» — для поставок старше данных о продажах в сервисе: сколько штук продано до того, как сервис стал их
видеть. В шаблоне — колонкой, при загрузке PDF — полем на предпросмотре.
"""
import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import supplier_lots as eng
from app.engine.offer_match import normalize
from app.integrations import supplier_docs
from app.integrations.supplier_docs import Doc, DocError, DocLine, parse_qty
from app.models.supplier_lot import SupplierDocument, SupplierLot, SupplierLotLine
from app.services import planning as planning_svc
from app.services import supplier_lots as svc
from app.services.costs import load_cost_history
from app.services.operation_import import _text
from app.services.sync.finance import today_msk
from app.services.templates import (
    CellError,
    Column,
    TemplateError,
    TemplateSpec,
    build_file,
    is_empty,
    parse_day,
    parse_key,
    read_table,
)

MAX_ROWS = 20_000
MAX_FILE_NAME = 255

SPEC = TemplateSpec(
    sheet="Поставки",
    columns=(
        Column("number", "№ поставки", width=14, exclude=("дата",),
               aliases=("№ поставки", "номер поставки", "№ отгрузки", "номер отгрузки", "№ документа", "номер документа"),
               hint="Номер документа поставщика (отгрузки, накладной). Строки с одним номером и датой — одна поставка."),
        Column("day", "Дата поставки", kind="date", width=14,
               aliases=("дата поставки", "дата отгрузки", "дата документа", "дата"),
               hint="Дата документа, ДД.ММ.ГГГГ. Продажи раньше этой даты в поставку не идут."),
        Column("supplier", "Поставщик", width=30, aliases=("поставщик", "организация"),
               hint="Необязательно: название поставщика."),
        Column("article", "Артикул", width=26, aliases=("артикул",),
               hint="Как в документе поставщика: сопоставляем с товаром только по артикулу."),
        Column("name", "Наименование", width=30, aliases=("наименован", "номенклатур"),
               hint="Необязательно: для вас, на расчёт не влияет."),
        Column("qty", "Количество, шт.", width=14, aliases=("количеств", "кол-во"), exclude=("продано",),
               hint="Сколько штук в поставке — целое число больше 0."),
        Column("sold_before", "Уже продано ранее, шт.", width=18, aliases=("уже продано", "продано ранее"),
               hint="Только для старых поставок: сколько штук продано до того, как сервис стал видеть продажи. "
                    "Пусто — 0."),
    ),
    required=(("number",), ("day",), ("article",), ("qty",)),
    missing_columns=(
        "Не найдены колонки «№ поставки», «Дата поставки», «Артикул» и «Количество». Скачайте шаблон на странице "
        "«Финансы → Долги поставщикам» — или загрузите документ поставщика в PDF."
    ),
    instructions=(
        "1. Одна строка — один артикул поставки. Строки с одинаковыми «№ поставки» и «Дата поставки» — одна поставка.",
        "2. Цену писать не нужно: её возьмём из справочника себестоимости на дату поставки, с НДС поставщика.",
        "3. «Уже продано ранее» — только для поставок, часть которых продана до подключения сервиса.",
        "4. Сохраните файл и загрузите на странице «Финансы → Долги поставщикам». Сначала покажем, что получится, — "
        "применится только после подтверждения.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и не удаляйте строку заголовков — по ним мы находим колонки.",
        "• Количество — целое число штук: «17», а не «17 шт., 2 упаковки».",
    ),
)


@dataclass
class DocPreview:
    doc: Doc
    status: str = "new"  # new | changed | unchanged | error
    lot: SupplierLot | None = None
    lines: list[dict] = field(default_factory=list)


def is_pdf(content: bytes, filename: str) -> bool:
    return content[:5] == b"%PDF-" or filename.lower().endswith(".pdf")


def parse_upload(content: bytes, filename: str) -> tuple[list[Doc], str]:
    if is_pdf(content, filename):
        return [supplier_docs.parse_pdf(content)], "pdf"
    try:
        table = read_table(SPEC, content, filename, MAX_ROWS)
    except TemplateError as exc:
        raise DocError(str(exc)) from exc
    docs: dict[tuple, Doc] = {}
    for t in table.rows:
        raw = t.values
        errors: list[str] = []

        def cell(key: str, parser, label: str, raw=raw, errors=errors) -> Any:
            if key not in raw:
                return None
            try:
                return parser(raw[key])
            except (CellError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
                return None

        number = cell("number", lambda v: None if is_empty(v) else parse_key(v, "№ поставки")[:64], "№ поставки")
        day = cell("day", parse_day, "дата поставки")
        supplier = cell("supplier", lambda v: _text(v, 300), "поставщик")
        article = cell("article", lambda v: None if is_empty(v) else parse_key(v), "артикул")
        name = cell("name", lambda v: _text(v, 300), "наименование")
        qty = cell("qty", parse_qty, "количество")
        sold = cell("sold_before", lambda v: 0 if is_empty(v) else parse_qty(v, allow_zero=True), "уже продано ранее") or 0
        if not number:
            errors.append("не указан № поставки")
        if day is None and not any(e.startswith("дата") for e in errors):
            errors.append("не указана дата поставки")
        if not article:
            errors.append("нет артикула")
        doc = docs.setdefault((number, day, supplier), Doc(number=number, day=day, supplier=supplier,
                                                          source=table.format))
        text = "; ".join(errors)
        doc.lines.append(DocLine(line=t.line, article=article or "", qty=qty, name=name, sold_before=sold,
                                 error=(text[:1].upper() + text[1:]) if text else None))
    if not docs:
        raise DocError("В файле нет строк с поставками")
    return [supplier_docs.check(d) for d in docs.values()], table.format


async def preview(
    db: AsyncSession, user_id: uuid.UUID, docs: list[Doc], sold_before: dict[str, int] | None = None
) -> list[DocPreview]:
    """Сопоставить строки с каталогом, цены, статус против уже загруженных поставок."""
    sold_before = sold_before or {}
    today = today_msk()
    costs = await load_cost_history(db, user_id)
    cat = await svc.catalog(db, user_id, costs)
    existing = {svc.lot_key(r.supplier, r.number, r.day): r for r in await svc.lots(db, user_id)}
    old_lines = await svc.lines_of(db, list(existing.values()))
    vat_cache: dict[date, Decimal] = {}
    seen: Counter = Counter()
    out = []
    for doc in docs:
        p = DocPreview(doc)
        out.append(p)
        if doc.day is not None and doc.day > today:
            doc.errors.append("Дата документа позже сегодняшнего дня — проверьте дату")
        if doc.day is not None and doc.day < planning_svc.MIN_DAY:
            doc.errors.append("Дата документа раньше 2000 года — проверьте дату")
        key = svc.lot_key(doc.supplier, doc.number, doc.day) if doc.number and doc.day else None
        if key is not None:
            seen[key] += 1
            if seen[key] > 1:
                doc.errors.append(f"Поставка № {doc.number} от {doc.day:%d.%m.%Y} в файле дважды — оставьте одну")
        p.lot = existing.get(key) if key else None
        previous = {normalize(ln.article): ln.sold_before for ln in old_lines.get(p.lot.id, [])} if p.lot else {}
        for ln in doc.lines:
            given = sold_before.get(f"{doc.number}|{doc.day}|{ln.article}")
            if given is not None:
                ln.sold_before = given
            elif ln.sold_before == 0 and doc.source == "pdf":
                ln.sold_before = previous.get(normalize(ln.article), 0)
            if ln.error is None and ln.qty is not None and not 0 <= ln.sold_before <= ln.qty:
                ln.error = f"«Уже продано ранее» — от 0 до {ln.qty} шт."
            offer = cat.offer(ln.article) if ln.article else None
            price = await svc.price_of(db, user_id, costs, offer, doc.day, vat_cache) if doc.day and offer else None
            p.lines.append({
                "line": ln.line, "article": ln.article, "name": ln.name, "barcode": ln.barcode, "qty": ln.qty,
                "sold_before": ln.sold_before, "offer": offer,
                "in_catalog": bool(ln.article) and cat.key(ln.article) in cat.in_catalog,
                "price": None if price is None else eng.q(price),
                "amount": None if price is None or ln.qty is None else eng.q(price * ln.qty), "error": ln.error,
            })
        if doc.errors or any(ln.error for ln in doc.lines):
            p.status = "error"
        elif p.lot is None:
            p.status = "new"
        else:
            p.status = "unchanged" if _same(old_lines.get(p.lot.id, []), doc.lines) else "changed"
    return out


def _same(old: list[SupplierLotLine], new: list[DocLine]) -> bool:
    a = sorted((normalize(ln.article), ln.qty, ln.sold_before) for ln in old)
    b = sorted((normalize(ln.article), ln.qty, ln.sold_before) for ln in new)
    return a == b


def preview_out(p: DocPreview, sales_from: date | None) -> dict:
    d = p.doc
    known = [ln["amount"] for ln in p.lines if ln["amount"] is not None]
    return {
        "number": d.number, "day": d.day, "supplier": d.supplier, "receiver": d.receiver, "warehouse": d.warehouse,
        "total_qty": d.total_qty, "lines_qty": d.lines_qty, "status": p.status,
        "lot_id": p.lot.lot_id if p.lot else None, "errors": d.errors, "warnings": d.warnings, "lines": p.lines,
        "amount": eng.q(sum(known, Decimal(0))), "no_price": sum(1 for ln in p.lines if ln["price"] is None),
        "not_in_catalog": sum(1 for ln in p.lines if ln["article"] and not ln["in_catalog"]),
        # поставка старше данных о продажах: часть могла продаться раньше — спросим «уже продано ранее»
        "older_than_sales": bool(d.day and sales_from and d.day < sales_from),
    }


async def apply(db: AsyncSession, user_id: uuid.UUID, previews: list[DocPreview], content: bytes, filename: str,
                fmt: str) -> dict[str, int]:
    counts = Counter(p.status for p in previews)
    todo = [p for p in previews if p.status in ("new", "changed")]
    if not todo:
        return dict(counts)
    stored = SupplierDocument(user_id=user_id, file_name=(filename or f"поставка.{fmt}")[:MAX_FILE_NAME],
                              file_size=len(content), sha256=hashlib.sha256(content).hexdigest(), content=content)
    db.add(stored)
    await db.flush()
    for p in todo:
        d = p.doc
        lines = [{"article": ln.article, "name": ln.name, "barcode": ln.barcode, "qty": ln.qty,
                  "sold_before": ln.sold_before, "written_off": 0} for ln in d.lines]
        header = {"number": d.number, "day": d.day, "supplier": d.supplier, "receiver": d.receiver,
                  "warehouse": d.warehouse, "doc_total_qty": d.total_qty, "source": fmt, "document_id": stored.id}
        if p.lot is None:
            row = SupplierLot(lot_id=uuid.uuid4(), version=1, user_id=user_id, **header)
            db.add(row)
            await db.flush()
            for ln in lines:
                db.add(SupplierLotLine(lot_row_id=row.id, **ln))
        else:  # состав поставки изменился — она снова открыта
            await svc.new_version(db, p.lot, lines, **header, closed_on=None, close_reason=None)
    await db.commit()
    return dict(counts)


async def template(db: AsyncSession, user_id: uuid.UUID, fmt: str, empty: bool) -> bytes:
    await svc.require_postpay(db, user_id)
    rows: list[dict[str, Any]] = []
    if not empty:
        lots = await svc.lots(db, user_id)
        lines = await svc.lines_of(db, lots)
        for lot in lots:
            for ln in lines[lot.id]:
                rows.append({"number": lot.number, "day": lot.day, "supplier": lot.supplier, "article": ln.article,
                             "name": ln.name, "qty": ln.qty, "sold_before": ln.sold_before or None})
    return build_file(fmt, SPEC, rows, list(SPEC.template_columns))
