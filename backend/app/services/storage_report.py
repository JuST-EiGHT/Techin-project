"""Хранение по товарам (шаг 4.2a): отчёт Ozon «Стоимость размещения по товарам» → доли хранения магазина за день.

Ozon начисляет хранение одной строкой в день на весь магазин («Размещение на складе», без товара), а сколько стоило
место каждого товара — отдаёт отдельным отчётом: `/v1/report/placement/by-products/create` (период до 31 дня, не больше
5 отчётов в день) → код → `/v1/report/info` → файл. Файл храним как документ (`ozon_documents`, правило 12), строки —
в `storage_costs`; по ним строка хранения за день делится между товарами (engine/product_pnl.storage_shares).

Загружается вместе с начислениями (синхронизация «Начисления», раз в 6 часов): за последние 31 день — раз в сутки, и
дни за 90 дней, которых ещё нет (для расчёта цены), — сами просим не больше DAILY_REQUESTS отчётов в день. Ozon
формирует отчёт не сразу: не готов за POLL_TRIES опросов — заберём при следующей синхронизации (код живёт 3 дня).
Файл не разобрался — документ с ошибкой; при следующей синхронизации пробуем разобрать его снова (парсер могли
исправить), заново у Ozon не просим.
"""
import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.product_pnl import SHARE_NEAR_DAYS, StorageShares
from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import OzonAPIError
from app.integrations.ozon_reports.placement import parse
from app.integrations.xlsx.common import ReportParseError
from app.models.product import Product
from app.models.report import DOC_ERROR, DOC_READY, REQUESTED, OzonDocument
from app.models.storage import PLACEMENT, StorageCost
from app.services.pnl_cache import cache
from app.services.sync.finance import MSK, today_msk

log = logging.getLogger(__name__)

CREATE_PATH = "/v1/report/placement/by-products/create"
WINDOW_DAYS = 31  # максимум периода у Ozon
HISTORY_DAYS = 90  # расчёт цены берёт хранение за 30 и 90 дней
DAILY_REQUESTS = 3  # Ozon даёт 5 отчётов в день — 2 оставляем на кабинет и ручные повторы
POLL_TRIES = 3
POLL_DELAY = 5.0  # секунд между опросами /v1/report/info
PARSE_ERROR = "Не удалось разобрать отчёт о размещении: "


def _requested_day(doc: OzonDocument) -> date:
    at = doc.requested_at if doc.requested_at.tzinfo else doc.requested_at.replace(tzinfo=timezone.utc)
    return at.astimezone(MSK).date()


def plan(today: date, docs: list[OzonDocument]) -> list[tuple[date, date]]:
    """Какие периоды попросить у Ozon: последние 31 день — если сегодня ещё не просили; затем дни за 90 дней, которых
    нет ни в готовом, ни в ждущем отчёте, — окнами по 31 день от самых новых."""
    yesterday = today - timedelta(days=1)
    recent = (yesterday - timedelta(days=WINDOW_DAYS - 1), yesterday)
    out: list[tuple[date, date]] = []
    if not any(_requested_day(d) == today and d.period_end == yesterday for d in docs):
        out.append(recent)
    covered: set[date] = set()
    for d in docs:
        if d.status in (DOC_READY, REQUESTED):
            covered.update(d.period_start + timedelta(days=i) for i in range((d.period_end - d.period_start).days + 1))
    covered.update(recent[0] + timedelta(days=i) for i in range(WINDOW_DAYS))
    start = today - timedelta(days=HISTORY_DAYS)
    missing = sorted((start + timedelta(days=i) for i in range((recent[0] - start).days)
                      if start + timedelta(days=i) not in covered), reverse=True)
    while missing:
        end = missing[0]
        first = max(end - timedelta(days=WINDOW_DAYS - 1), start)
        out.append((first, end))
        missing = [d for d in missing if d < first]
    return out


async def ingest(db: AsyncSession, doc: OzonDocument) -> int:
    """Разобрать файл документа в строки storage_costs (прежние строки документа — заменяются: они производные).
    Не разобрался — документ с ошибкой простыми словами. Возвращает число строк."""
    try:
        rows = parse(doc.content or b"")
    except ReportParseError as exc:
        doc.status, doc.error = DOC_ERROR, PARSE_ERROR + str(exc)
        await db.commit()
        return 0
    await db.execute(delete(StorageCost).where(StorageCost.document_id == doc.id))
    db.add_all(StorageCost(user_id=doc.user_id, document_id=doc.id, day=r.day, sku=r.sku, offer_id=r.offer_id,
                           amount=r.amount) for r in rows)
    doc.status, doc.error = DOC_READY, None
    await db.commit()
    await cache.invalidate(doc.user_id)  # прибыль по товарам за эти дни поделится иначе
    return len(rows)


async def _rows_count(db: AsyncSession, doc_id: uuid.UUID) -> int:
    return await db.scalar(select(func.count()).select_from(StorageCost).where(StorageCost.document_id == doc_id)) or 0


async def sync(db: AsyncSession, store_id: uuid.UUID, client: OzonClient, poll_delay: float = POLL_DELAY) -> dict:
    """Забрать готовые отчёты, разобрать неразобранные и попросить недостающие. Ошибки Ozon — в stats, не исключением:
    хранение по товарам не должно ронять загрузку начислений."""
    from app.services.ozon_reports import refresh_document  # ozon_reports импортирует синхронизации — импорт внутри

    stats: dict = {"requested": 0, "ready": 0, "rows": 0}
    docs = list((await db.execute(
        select(OzonDocument).where(OzonDocument.user_id == store_id, OzonDocument.doc_type == PLACEMENT)
        .order_by(OzonDocument.requested_at)
    )).scalars())

    async def settle(doc: OzonDocument) -> None:
        for attempt in range(POLL_TRIES):
            await refresh_document(db, doc, force=True, client=client)
            if doc.status != REQUESTED:
                break
            if attempt + 1 < POLL_TRIES and poll_delay:
                await asyncio.sleep(poll_delay)
        if doc.status == DOC_READY:
            stats["ready"] += 1
            stats["rows"] += await ingest(db, doc)

    for doc in [d for d in docs if d.status == REQUESTED]:
        await settle(doc)
    for doc in [d for d in docs if d.content and (d.status == DOC_ERROR or
                                                  (d.status == DOC_READY and not await _rows_count(db, d.id)))]:
        stats["rows"] += await ingest(db, doc)  # файл есть, строк нет — разбираем снова

    today = today_msk()
    quota = DAILY_REQUESTS - sum(1 for d in docs if _requested_day(d) == today)
    for date_from, date_to in plan(today, docs)[:max(quota, 0)]:
        try:
            resp = await client.request("POST", CREATE_PATH, {"date_from": date_from.isoformat(),
                                                              "date_to": date_to.isoformat()})
        except OzonAPIError as exc:
            stats["error"] = exc.message
            log.warning("Отчёт о размещении у %s не запрошен: %s (%s)", store_id, exc.message, exc.ozon_message)
            break
        code = (resp or {}).get("code") or ((resp or {}).get("result") or {}).get("code")
        if not code:
            stats["error"] = "Ozon не вернул код отчёта о размещении"
            break
        doc = OzonDocument(user_id=store_id, doc_type=PLACEMENT, period_start=date_from, period_end=date_to,
                           status=REQUESTED, ozon_code=str(code)[:255], requested_at=datetime.now(timezone.utc))
        db.add(doc)
        await db.commit()
        stats["requested"] += 1
        await settle(doc)
    return stats


async def shares(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date) -> StorageShares:
    """Доли хранения по товарам на каждый день периода (с запасом SHARE_NEAR_DAYS): день → артикул → стоимость
    размещения. За день — последний разобранный отчёт, который его покрывает. Товар — по SKU в каталоге магазина, нет
    SKU — по артикулу, если такой товар есть; чужие и неизвестные строки не берём (не плодим «товары-призраки»)."""
    lo, hi = date_from - timedelta(days=SHARE_NEAR_DAYS), date_to + timedelta(days=SHARE_NEAR_DAYS)
    docs = list((await db.execute(
        select(OzonDocument).where(
            OzonDocument.user_id == store_id, OzonDocument.doc_type == PLACEMENT, OzonDocument.status == DOC_READY,
            OzonDocument.period_start <= hi, OzonDocument.period_end >= lo,
        ).order_by(OzonDocument.ready_at, OzonDocument.requested_at)
    )).scalars())
    if not docs:
        return {}
    rows = list((await db.execute(
        select(StorageCost).where(StorageCost.document_id.in_([d.id for d in docs]))
    )).scalars())
    by_doc: dict[uuid.UUID, list[StorageCost]] = {}
    for r in rows:
        by_doc.setdefault(r.document_id, []).append(r)
    catalog = (await db.execute(select(Product.ozon_sku, Product.offer_id).where(Product.user_id == store_id))).all()
    offer_of = {sku: offer for sku, offer in catalog if sku is not None}
    known = {offer for _, offer in catalog}

    out: StorageShares = {}
    for doc in docs:
        days = [doc.period_start + timedelta(days=i) for i in range((doc.period_end - doc.period_start).days + 1)]
        day_map: dict[date, dict[str, Decimal]] = {}
        for r in by_doc.get(doc.id, []):
            offer = offer_of.get(r.sku) or (r.offer_id if r.offer_id in known else None)
            if not offer:
                continue
            spread = [r.day] if r.day else days
            part = Decimal(r.amount) / len(spread)
            for d in spread:
                weights = day_map.setdefault(d, {})
                weights[offer] = weights.get(offer, Decimal(0)) + part
        for d, weights in day_map.items():
            if lo <= d <= hi:
                out[d] = weights  # более поздний отчёт за день заменяет прежний
    return out
