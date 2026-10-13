"""Загруженные XLSX-отчёты: сохранение, конфликты периода, скрытие и восстановление, повторный разбор.

Исходный файл хранится всегда (docs/DATA_HISTORY.md): из него пересобираются итоги и строки
начислений — после исправления парсера, при восстановлении скрытого отчёта.
Строки отчёта по начислениям лежат в accruals (source='xlsx', область `report:<id>`) и есть
только у видимых отчётов: у скрытого их нет, иначе они попали бы в расчёты.
"""
import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.xlsx import AccrualLine, CostProvider, ParsedReport, ReportParseError, parse_report_full
from app.integrations.xlsx.common import norm
from app.integrations.xlsx.parsers.base import money
from app.models.accrual import SOURCE_XLSX, Accrual, AccrualType
from app.models.product import Product
from app.models.report import DEFAULT_TAX_RATE, DEFAULT_VAT_MULTIPLIER, ERROR, READY, Report, ReportFile

OnConflict = Literal["ask", "replace", "keep"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def period_label(r: Report) -> str:
    if r.period_start and r.period_end:
        return fmt_date(r.period_start) if r.period_start == r.period_end else (
            f"{fmt_date(r.period_start)}–{fmt_date(r.period_end)}"
        )
    return r.period or "период не указан"


@dataclass
class Conflict:
    report: Report
    same_file: bool


class ReportConflict(Exception):
    """Отчёт за этот период (или этот же файл) уже загружен — нужно решение пользователя."""

    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts
        same = [c.report for c in conflicts if c.same_file]
        if same:
            r = same[0]
            text = f"Этот файл уже загружен: «{r.filename}», {period_label(r)}, загружен {fmt_date(r.uploaded_at)}."
        else:
            names = ", ".join(f"«{c.report.filename}» ({period_label(c.report)})" for c in conflicts)
            many = len(conflicts) > 1
            text = f"За этот период уже есть {'отчёты' if many else 'отчёт'} {names}."
        super().__init__(f"{text} Заменить {'их' if len(conflicts) > 1 else 'его'} новым или оставить оба?")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def parse_file(content: bytes, costs: CostProvider, filename: str | None) -> ParsedReport:
    # openpyxl синхронный — разбор в отдельном потоке, чтобы не держать event loop
    return await asyncio.to_thread(parse_report_full, content, costs, filename)


async def find_conflicts(
    db: AsyncSession, user_id: uuid.UUID, parsed: ParsedReport, file_hash: str, exclude: uuid.UUID | None = None
) -> list[Conflict]:
    """Видимые отчёты с тем же файлом или того же типа с пересекающимся периодом."""
    data = parsed.data
    same_period = None
    if data.period_start and data.period_end:
        same_period = (
            (Report.report_type == data.report_type)
            & (Report.period_start <= data.period_end)
            & (Report.period_end >= data.period_start)
        )
    cond = Report.file_sha256 == file_hash if same_period is None else or_(Report.file_sha256 == file_hash, same_period)
    q = select(Report).where(Report.user_id == user_id, Report.deleted_at.is_(None), cond)
    if exclude:
        q = q.where(Report.id != exclude)
    found = (await db.execute(q.order_by(Report.uploaded_at))).scalars().all()
    return [Conflict(r, r.file_sha256 == file_hash) for r in found]


async def create_report(
    db: AsyncSession,
    user_id: uuid.UUID,
    filename: str,
    content: bytes,
    costs: CostProvider,
    on_conflict: OnConflict = "ask",
) -> Report:
    """Разобрать и сохранить отчёт. ReportParseError — файл не разобран (ничего не сохраняется),
    ReportConflict — такой период или файл уже есть, а on_conflict='ask'."""
    parsed = await parse_file(content, costs, filename)
    file_hash = sha256(content)
    conflicts = await find_conflicts(db, user_id, parsed, file_hash)
    if conflicts and on_conflict == "ask":
        raise ReportConflict(conflicts)

    now = _now()
    report = Report(
        id=uuid.uuid4(), user_id=user_id, filename=filename, file_sha256=file_hash, file_size=len(content),
        uploaded_at=now, vat_multiplier=DEFAULT_VAT_MULTIPLIER, tax_rate=DEFAULT_TAX_RATE,
    )
    _apply_parsed(report, parsed, now)
    db.add(report)
    db.add(ReportFile(report_id=report.id, content=content))
    await db.flush()
    if on_conflict == "replace":
        for c in conflicts:
            await _hide(db, c.report, now, replaced_by=report.id)
    await _write_accruals(db, report, parsed.accrual_lines)
    await db.commit()
    return report


def _apply_parsed(report: Report, parsed: ParsedReport, now: datetime) -> None:
    data = parsed.data
    report.report_type = data.report_type
    report.period = data.period[:100]
    report.period_start = data.period_start
    report.period_end = data.period_end
    report.summary_json = data.model_dump(mode="json")
    report.status = READY
    report.error_message = None
    report.parsed_at = now


async def _write_accruals(db: AsyncSession, report: Report, lines: list[AccrualLine]) -> None:
    """Строки отчёта по начислениям → accruals. Область отчёта заменяется целиком."""
    await db.execute(delete(Accrual).where(Accrual.report_id == report.id))
    if not lines:
        return
    by_offer: dict[str, uuid.UUID] = {}
    by_sku: dict[int, uuid.UUID] = {}
    for pid, offer_id, sku in await db.execute(
        select(Product.id, Product.offer_id, Product.ozon_sku).where(Product.user_id == report.user_id)
    ):
        by_offer[offer_id] = pid
        if sku:
            by_sku[sku] = pid
    types = {norm(name): tid for tid, name in await db.execute(select(AccrualType.id, AccrualType.name_ru))}

    undated = 0
    for line in lines:
        day = line.accrual_date or report.period_start
        if day is None:
            undated += 1
            continue
        db.add(Accrual(
            user_id=report.user_id,
            product_id=by_offer.get(line.offer_id) or (by_sku.get(line.sku) if line.sku else None),
            posting_number=line.posting_number[:64] or None,
            sku=line.sku,
            accrual_type_id=types.get(norm(line.type_name)),
            type_name=line.type_name[:255],
            offer_id=line.offer_id[:100] or None,
            accrual_date=day,
            amount=money(line.amount),
            quantity=line.quantity,
            source=SOURCE_XLSX,
            sync_scope=f"report:{report.id}",
            line_no=line.row_no,
            raw_json=line.raw,
            report_id=report.id,
        ))
    if undated:
        summary = dict(report.summary_json)
        summary["warnings"] = [
            *summary.get("warnings", []),
            f"Строк без даты начисления: {undated} — в итоги отчёта они вошли, но в учёт по дням не попали.",
        ]
        report.summary_json = summary


async def _hide(db: AsyncSession, report: Report, now: datetime, replaced_by: uuid.UUID | None = None) -> None:
    report.deleted_at = now
    report.replaced_by_id = replaced_by
    await db.execute(delete(Accrual).where(Accrual.report_id == report.id))


async def hide_report(db: AsyncSession, report: Report) -> None:
    await _hide(db, report, _now())
    await db.commit()


async def _file(db: AsyncSession, report: Report) -> bytes:
    stored = await db.get(ReportFile, report.id)
    if stored is None:  # не должно случаться: файл пишется вместе с отчётом
        raise ReportParseError("Исходный файл отчёта не найден — загрузите отчёт заново.")
    return stored.content


async def restore_report(db: AsyncSession, report: Report, costs: CostProvider) -> Report:
    """Вернуть скрытый отчёт. Строки начислений пересобираются из файла."""
    parsed = await parse_file(await _file(db, report), costs, report.filename)
    _apply_parsed(report, parsed, _now())
    report.deleted_at = None
    report.replaced_by_id = None
    await _write_accruals(db, report, parsed.accrual_lines)
    await db.commit()
    return report


async def reparse_report(db: AsyncSession, report: Report, costs: CostProvider) -> Report:
    """Перечитать сохранённый файл текущим парсером и себестоимостью. Ручные правки остаются.
    Не удалось — итоги прежние, статус error с текстом ошибки, исключение наружу."""
    try:
        parsed = await parse_file(await _file(db, report), costs, report.filename)
    except ReportParseError as exc:
        report.status = ERROR
        report.error_message = str(exc)
        await db.commit()
        raise
    _apply_parsed(report, parsed, _now())
    await _write_accruals(db, report, parsed.accrual_lines)
    await db.commit()
    return report


async def get_report(
    db: AsyncSession, user_id: uuid.UUID, report_id: uuid.UUID, deleted: bool = False
) -> Report | None:
    report = await db.get(Report, report_id)
    if report is None or report.user_id != user_id or (report.deleted_at is not None) != deleted:
        return None
    return report


async def list_reports(
    db: AsyncSession, user_id: uuid.UUID, report_type: str | None, limit: int, offset: int
) -> tuple[list[Report], int]:
    q = select(Report).where(Report.user_id == user_id, Report.deleted_at.is_(None))
    if report_type:
        q = q.where(Report.report_type == report_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = await db.execute(
        q.order_by(Report.period_start.desc().nulls_last(), Report.uploaded_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars()), total


def effective_total(report: Report) -> Decimal:
    return report.manual_total if report.manual_total is not None else Decimal(report.summary_json["auto_total"])
