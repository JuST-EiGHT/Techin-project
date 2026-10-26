"""Отчёты Ozon через API и полнота данных (шаг 10.12).

- Отчёт о реализации за месяц — `/v2/finance/realization`: ответ сохраняется как есть (report_files, правило 12) и
  разбирается в тот же вид, что XLSX (`integrations/ozon_reports`) — дальше это обычный отчёт: история, конфликт
  периода, скрытие, перечитывание, P&L по отчёту. Отчёт за месяц, который уже есть, — тот же выбор, что при загрузке
  файла: заменить или оставить оба.
- Документы, которые Ozon отдаёт файлом (взаиморасчёты, компенсации, декомпенсации): запрос → код → `/v1/report/info`
  (статус и ссылка) → файл скачиваем и храним (`ozon_documents`). Опрашиваем, пока страница открыта (не чаще раза в
  10 секунд на документ); код живёт у Ozon 3 дня.
- Полнота: по дням — покрытие P&L (3.2: файл или API), по неделям и месяцам — `engine/report_coverage`.
"""
import json
import posixpath
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import report_catalog as rc
from app.engine import report_coverage as cov
from app.integrations.ozon.errors import OzonAPIError, OzonAuthError, OzonNotConfiguredError
from app.integrations.ozon.factory import get_ozon_client
from app.integrations.ozon_reports import REALIZATION_SUFFIX
from app.integrations.xlsx import CostProvider
from app.models.report import API, DOC_ERROR, DOC_READY, REQUESTED, OzonDocument, Report
from app.services import pnl_data
from app.services import reports as report_svc
from app.services.sync.finance import today_msk

FIRST_REALIZATION = date(2023, 8, 1)  # раньше Ozon через API не отдаёт — только в кабинете
DOCUMENT_PATHS = {
    "mutual_settlement": "/v1/finance/mutual-settlement",
    "compensation": "/v1/finance/compensation",
    "decompensation": "/v1/finance/decompensation",
}
CODE_TTL = timedelta(days=3)  # код отчёта у Ozon живёт 3 дня
POLL_EVERY = timedelta(seconds=10)
MAX_FILE = 50 * 1024 * 1024
FILE_HOSTS = ("ozone.ru", "ozon.ru")  # файлы отчётов Ozon отдаёт со своих доменов — другие ссылки не скачиваем
REPORT_STATES = {"waiting": "в очереди у Ozon", "processing": "Ozon формирует"}


class OzonReportsError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message, self.code = message, code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _month(year: int, month: int, today: date, earliest: date | None = None) -> tuple[date, date]:
    """Закончившийся месяц: (первый, последний день)."""
    try:
        first = date(year, month, 1)
    except ValueError:
        raise OzonReportsError("Месяц указан неверно") from None
    if first >= cov.month_start(today):
        raise OzonReportsError("Отчёт за месяц можно получить, когда месяц закончится")
    if earliest and first < earliest:
        raise OzonReportsError(f"Через API Ozon отдаёт отчёт с {earliest:%m.%Y} — за более ранние месяцы скачайте его в "
                               "личном кабинете и загрузите файлом")
    return first, cov.month_end(first)


def _ozon_error(exc: OzonAPIError, not_found: str | None = None) -> OzonReportsError:
    if exc.status_code == 404 and not_found:
        return OzonReportsError(not_found, 404)
    if isinstance(exc, OzonNotConfiguredError | OzonAuthError):  # ключ — исправит пользователь
        return OzonReportsError(exc.message, 400)
    return OzonReportsError(exc.message, 502 if exc.status_code is None or exc.status_code >= 500 else 400)


# ── отчёт о реализации ──
async def fetch_realization(db: AsyncSession, store_id: uuid.UUID, year: int, month: int, costs: CostProvider,
                            on_conflict: report_svc.OnConflict = "ask") -> Report:
    """Получить отчёт о реализации за месяц через API и сохранить как отчёт (source=api). ReportConflict — за этот месяц
    отчёт уже есть (решает пользователь), ReportParseError — строк нет."""
    _month(year, month, today_msk(), FIRST_REALIZATION)
    try:
        async with await get_ozon_client(store_id, db) as client:
            payload = await client.request("POST", "/v2/finance/realization", {"month": month, "year": year})
    except OzonAPIError as exc:
        raise _ozon_error(exc, "Ozon ещё не сформировал отчёт о реализации за этот месяц — он появляется до 5-го числа "
                               "следующего месяца. Попробуйте позже.") from None
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    name = f"Ozon API {year}-{month:02d}{REALIZATION_SUFFIX}"
    return await report_svc.create_report(db, store_id, name, content, costs, on_conflict, source=API)


# ── документы файлом ──
async def request_document(db: AsyncSession, store_id: uuid.UUID, doc_type: str, year: int, month: int) -> OzonDocument:
    if doc_type not in DOCUMENT_PATHS:
        raise OzonReportsError("Такой документ через API не запрашивается")
    first, last = _month(year, month, today_msk())
    try:
        async with await get_ozon_client(store_id, db) as client:
            resp = await client.request("POST", DOCUMENT_PATHS[doc_type],
                                        {"date": f"{year}-{month:02d}", "language": "RU"})
    except OzonAPIError as exc:
        raise _ozon_error(exc) from None
    code = ((resp or {}).get("result") or {}).get("code")
    if not code:
        raise OzonReportsError("Ozon не вернул код документа — попробуйте ещё раз позже", 502)
    doc = OzonDocument(user_id=store_id, doc_type=doc_type, period_start=first, period_end=last, status=REQUESTED,
                       ozon_code=str(code)[:255], requested_at=_now())
    db.add(doc)
    await db.commit()
    return doc


def _utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe_url(url: str) -> bool:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in FILE_HOSTS)


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
        r = await http.get(url)
    if r.status_code != 200:
        raise OzonReportsError(f"Ozon не отдал файл (HTTP {r.status_code}) — запросите документ заново")
    if len(r.content) > MAX_FILE:
        raise OzonReportsError("Файл больше 50 МБ — скачайте его в личном кабинете Ozon")
    return r.content


async def refresh_document(db: AsyncSession, doc: OzonDocument, force: bool = False) -> OzonDocument:
    """Спросить Ozon о готовности: готов — скачать файл; не вышло — ошибка простыми словами. Готовый и с ошибкой — как
    есть; чаще раза в 10 секунд не спрашиваем."""
    now = _now()
    if doc.status != REQUESTED or (not force and doc.checked_at and now - _utc(doc.checked_at) < POLL_EVERY):
        return doc
    if now - _utc(doc.requested_at) > CODE_TTL:
        doc.status, doc.error = DOC_ERROR, "Ozon не сформировал документ за 3 дня — запросите его заново"
        await db.commit()
        return doc
    doc.checked_at = now
    try:
        async with await get_ozon_client(doc.user_id, db) as client:
            resp = await client.request("POST", "/v1/report/info", {"code": doc.ozon_code})
    except OzonAPIError as exc:  # временная ошибка — спросим ещё раз при следующем открытии
        doc.error = exc.message
        await db.commit()
        return doc
    info = (resp or {}).get("result") or {}
    doc.info_json = info
    state = str(info.get("status") or "").lower()
    if state == "failed":
        doc.status = DOC_ERROR
        doc.error = "Ozon не смог сформировать документ" + (f" ({info.get('error')})" if info.get("error") else "") + \
            " — запросите его ещё раз позже"
    elif state == "success" and info.get("file"):
        url = str(info["file"])
        if not _safe_url(url):
            doc.status, doc.error = DOC_ERROR, "Ozon вернул ссылку на файл не со своего сайта — файл не скачан"
        else:
            try:
                content = await _download(url)
            except (OzonReportsError, httpx.HTTPError) as exc:
                doc.error = exc.message if isinstance(exc, OzonReportsError) else "Нет связи с Ozon — повторим позже"
            else:
                ext = posixpath.splitext(urlparse(url).path)[1] or ".xlsx"
                doc.content, doc.file_size, doc.ready_at = content, len(content), now
                doc.file_name = f"{rc.BY_KEY[doc.doc_type].title} {doc.period_start:%m.%Y}{ext}"
                doc.status, doc.error = DOC_READY, None
    else:
        doc.error = None  # ждём: waiting / processing
    await db.commit()
    return doc


async def list_documents(db: AsyncSession, store_id: uuid.UUID, limit: int = 50) -> list[OzonDocument]:
    """Документы — новые сверху; ждущие ответа Ozon — заодно спрашиваем (не больше 5 за раз)."""
    docs = list((await db.execute(
        select(OzonDocument).where(OzonDocument.user_id == store_id)
        .order_by(OzonDocument.requested_at.desc()).limit(limit)
    )).scalars())
    for doc in [d for d in docs if d.status == REQUESTED][:5]:
        await refresh_document(db, doc)
    return docs


async def get_document(db: AsyncSession, store_id: uuid.UUID, doc_id: uuid.UUID) -> OzonDocument:
    doc = await db.get(OzonDocument, doc_id)
    if doc is None or doc.user_id != store_id:
        raise OzonReportsError("Документ не найден", 404)
    return doc


def document_out(doc: OzonDocument) -> dict:
    kind = rc.BY_KEY[doc.doc_type]
    state = REPORT_STATES.get(str((doc.info_json or {}).get("status") or "").lower(), "запрошен")
    return {
        "id": str(doc.id), "doc_type": doc.doc_type, "title": kind.title, "period_start": doc.period_start,
        "period_end": doc.period_end, "status": doc.status, "state_text": state if doc.status == REQUESTED else None,
        "requested_at": _utc(doc.requested_at), "ready_at": _utc(doc.ready_at), "error": doc.error,
        "file_name": doc.file_name, "file_size": doc.file_size,
    }


# ── каталог и полнота ──
def catalog_out(finance_only: bool) -> list[dict]:
    return [{
        "key": k.key, "title": k.title, "what": k.what, "cabinet": k.cabinet, "how": list(k.how),
        "how_titles": [rc.HOW_TITLES[h] for h in k.how], "api": list(k.api), "gives": k.gives, "used_for": k.used_for,
        "finance": k.finance, "link": k.link,
    } for k in rc.catalog(finance_only)]


async def coverage(db: AsyncSession, store_id: uuid.UUID, months: int = 6) -> dict:
    """Чем закрыты последние месяцы: недели (файл / API / дыры) и отчёт о реализации."""
    today = today_msk()
    start = cov.add_months(cov.month_start(today), -(months - 1))
    days = (await pnl_data.coverage(db, store_id, start, today, "auto")).days
    realization: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for r in (await db.execute(select(Report).where(
        Report.user_id == store_id, Report.deleted_at.is_(None), Report.report_type == "realization",
        Report.period_start.is_not(None), Report.period_start <= today, Report.period_end >= start,
    ))).scalars():
        realization.setdefault((r.period_start.year, r.period_start.month), []).append((r.source, str(r.id)))
    out = []
    for m in cov.months(days, realization, today, months):
        out.append({
            "year": m.year, "month": m.month, "title": m.title, "date_from": m.start, "date_to": m.end,
            "days": m.days, "missing": m.missing, "realization": m.realization,
            "realization_sources": m.realization_sources, "realization_ids": m.realization_ids,
            "weeks": [{"date_from": w.start, "date_to": w.end, "days": w.days, "xlsx": w.xlsx, "api": w.api,
                       "missing": len(w.missing), "state": w.state,
                       "missing_ranges": [{"date_from": a, "date_to": b} for a, b in pnl_data.ranges(w.missing)]}
                      for w in m.weeks],
        })
    return {"today": today, "months": out}
