"""Лента «История изменений и событий» (шаг 10.11): события магазина из уже хранимых данных — по времени назад, с
фильтрами по периоду, виду и товару (или категории), страницами по курсору.

Источники (каждый — запрос «последние K до курсора» по индексу времени; тексты — engine/event_feed):
- цены через сервис — `price_changes` (кроме предпросмотров): вручную, откат, правило; «списком» — одним событием на
  массовое изменение, а при фильтре товара — по товару;
- цены не через сервис — снимки цены (`product_prices`), цена в которых отличается от прошлой, если в эти же сутки
  сервис не применял именно эту цену (тогда это снимок его же изменения);
- акции — вход (`first_seen_at`, «Ozon добавил сам») и выход (`left_at`) из участия;
- остатки — товар закончился на всех складах и снова появился (пришла поставка): по снимкам остатков; первый снимок
  товара только запоминаем; изменения на нескольких складах в одну минуту — одно состояние;
- аномалии — появилась (с важностью) и прошла (кроме «стала красной» — тогда появляется новая, красная);
- действия — журнал запросов (`audit_log`, только успешные), понятным текстом; загрузка отчёта — по самому отчёту;
- ошибки загрузки — начало сбоя синхронизации (прошлый запуск того же вида был успешным), отчёт с ошибкой перечитывания,
  файл отчёта не загрузился.
Фильтр товара или категории оставляет события этих товаров (у действий — правка их себестоимости).
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.engine import event_feed as ef
from app.engine.anomaly_text import SYNC_WINDOW, TYPE_TITLES, describe
from app.models.anomaly import ESCALATED, RED, Anomaly
from app.models.audit import HTTP_ENTITY, OK, AuditLog
from app.models.price_change import APPLIED, MANUAL_BULK, PREVIEW, QUEUED, UNKNOWN, PriceChange
from app.models.product import Product, ProductPrice
from app.models.promotion import Promotion, PromotionProduct
from app.models.report import ERROR as REPORT_ERROR
from app.models.report import Report
from app.models.sync_run import FAILED, SyncRun
from app.models.warehouse import ProductStock
from app.services.analytics import AnalyticsError, _products
from app.services.sync import overview
from app.services.sync.finance import MSK, today_msk

DEFAULT_LIMIT, MAX_LIMIT = 50, 200
SERVICE_PRICE = (APPLIED, UNKNOWN, QUEUED)  # дошло до Ozon — снимок той же цены не отдельное событие


class Ctx:
    """Параметры запроса: магазин, товары фильтра (None — все), граница «до» и «не раньше», сколько брать."""

    def __init__(self, store_id: uuid.UUID, products: list[Product] | None, before: datetime | None,
                 since: datetime | None, until: datetime | None, k: int):
        # всё — в UTC: SQLite тестов сравнивает время строкой без пояса
        before, since, until = (None if v is None else v.astimezone(timezone.utc) for v in (before, since, until))
        self.store_id, self.products, self.since, self.k = store_id, products, since, k
        self.product_ids = None if products is None else [p.id for p in products]
        bounds = [b for b in (before, until) if b is not None]
        self.before = min(bounds) if bounds else None  # события не позже этого момента

    def upto(self, column):
        """Не позже курсора (равные отсеет страница по ключу) и не раньше начала периода."""
        conds = []
        if self.before is not None:
            conds.append(column <= self.before)
        if self.since is not None:
            conds.append(column >= self.since)
        return and_(*conds) if conds else true()

    def of_products(self, column):
        return true() if self.product_ids is None else column.in_(self.product_ids)


def _ref(p: Product) -> ef.ProductRef:
    return ef.ProductRef(str(p.id), p.offer_id, p.name)


async def _catalog(db: AsyncSession, ids) -> dict[uuid.UUID, Product]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {p.id: p for p in (await db.execute(select(Product).where(Product.id.in_(ids)))).scalars()}


# ── цены ──
async def _price_changes(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    q = (select(PriceChange).where(PriceChange.user_id == c.store_id, PriceChange.status != PREVIEW,
                                   c.upto(PriceChange.created_at), c.of_products(PriceChange.product_id))
         .order_by(PriceChange.created_at.desc()).limit(c.k))
    if c.product_ids is None:  # списком — одним событием на изменение (ниже)
        q = q.where(or_(PriceChange.change_type != MANUAL_BULK, PriceChange.batch_id.is_(None)))
    rows = list((await db.execute(q)).scalars())
    catalog = await _catalog(db, [r.product_id for r in rows])
    out = []
    for r in rows:
        p = catalog.get(r.product_id)
        ref = _ref(p) if p else ef.ProductRef(str(r.product_id), r.offer_id, None)
        out.append(ef.price_change(f"price:{r.id}", ef.utc(r.created_at), ref, r.change_type, r.status, r.price_before,
                                   r.price_after, r.min_price_before, r.min_price_after, r.rollback_of_id is not None,
                                   r.reason, r.error))
    return out, len(rows) == c.k


async def _price_batches(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    if c.product_ids is not None:
        return [], False
    at = func.max(PriceChange.created_at)
    applied = func.sum(case((PriceChange.status == APPLIED, 1), else_=0))
    q = (select(PriceChange.batch_id, at, func.count(), applied)
         .where(PriceChange.user_id == c.store_id, PriceChange.change_type == MANUAL_BULK,
                PriceChange.batch_id.is_not(None), PriceChange.status != PREVIEW)
         .group_by(PriceChange.batch_id).order_by(at.desc()).limit(c.k))
    if c.before is not None:
        q = q.having(at <= c.before)
    if c.since is not None:
        q = q.having(at >= c.since)
    rows = (await db.execute(q)).all()
    return [ef.price_batch(f"bulk:{b}", ef.utc(t), n, int(a or 0)) for b, t, n, a in rows], len(rows) == c.k


async def _price_cabinet(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    prev = aliased(ProductPrice)
    prev_price = (select(prev.price).where(prev.product_id == ProductPrice.product_id,
                                           prev.snapshot_at < ProductPrice.snapshot_at)
                  .order_by(prev.snapshot_at.desc()).limit(1).correlate(ProductPrice).scalar_subquery())
    q = (select(ProductPrice, Product, prev_price.label("before"))
         .join(Product, Product.id == ProductPrice.product_id)
         .where(Product.user_id == c.store_id, c.of_products(ProductPrice.product_id), c.upto(ProductPrice.snapshot_at),
                ProductPrice.price.is_not(None), ProductPrice.price > 0,
                prev_price.is_not(None), prev_price > 0, prev_price != ProductPrice.price)
         .order_by(ProductPrice.snapshot_at.desc()).limit(c.k))
    rows = (await db.execute(q)).all()
    if not rows:
        return [], False
    # снимок изменения, которое применил сервис (та же цена в те же сутки), — не отдельное событие
    oldest = min(ef.utc(s.snapshot_at) for s, _, _ in rows) - timedelta(days=1)
    service = {}
    for pid, price, at in await db.execute(
        select(PriceChange.product_id, PriceChange.price_after, PriceChange.created_at)
        .where(PriceChange.user_id == c.store_id, PriceChange.status.in_(SERVICE_PRICE),
               PriceChange.product_id.in_({s.product_id for s, _, _ in rows}), PriceChange.created_at >= oldest)
    ):
        service.setdefault((pid, price), []).append(ef.utc(at))
    out = []
    for s, p, before in rows:
        at = ef.utc(s.snapshot_at)
        if any(timedelta(0) <= at - t <= timedelta(days=1) for t in service.get((s.product_id, s.price), [])):
            continue
        out.append(ef.price_cabinet(f"snap:{s.id}", at, _ref(p), before, s.price))
    return out, len(rows) == c.k


# ── акции ──
async def _promotions(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    out, full = [], False
    for column in (PromotionProduct.first_seen_at, PromotionProduct.left_at):
        q = (select(PromotionProduct, Promotion, Product)
             .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
             .join(Product, Product.id == PromotionProduct.product_id)
             .where(Promotion.user_id == c.store_id, PromotionProduct.is_active.is_(True), column.is_not(None),
                    c.upto(column), c.of_products(PromotionProduct.product_id))
             .order_by(column.desc()).limit(c.k))
        rows = (await db.execute(q)).all()
        full = full or len(rows) == c.k
        for pp, promo, p in rows:
            if column is PromotionProduct.first_seen_at:
                out.append(ef.promo_in(f"promo-in:{pp.id}", ef.utc(pp.first_seen_at), _ref(p), str(promo.id), promo.title,
                                       pp.added_automatically, pp.action_price))
            else:
                out.append(ef.promo_out(f"promo-out:{pp.id}", ef.utc(pp.left_at), _ref(p), str(promo.id), promo.title))
    return out, full


# ── остатки ──
def _total_present(product_col, at_col, strict: bool):
    """Сумма остатка по складам товара на момент (строго до него — strict) — по последнему снимку каждого склада."""
    s2, s3 = aliased(ProductStock), aliased(ProductStock)
    last = (select(func.max(s3.snapshot_at)).where(s3.product_id == s2.product_id, s3.warehouse_id == s2.warehouse_id,
                                                   s3.snapshot_at < at_col if strict else s3.snapshot_at <= at_col)
            .correlate(s2, ProductStock).scalar_subquery())
    return (select(func.coalesce(func.sum(s2.present), 0)).where(s2.product_id == product_col, s2.snapshot_at == last)
            .correlate(ProductStock).scalar_subquery())


async def _stock(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    s = ProductStock
    prev = aliased(ProductStock)
    prev_present = (select(prev.present).where(prev.product_id == s.product_id, prev.warehouse_id == s.warehouse_id,
                                               prev.snapshot_at < s.snapshot_at)
                    .order_by(prev.snapshot_at.desc()).limit(1).correlate(s).scalar_subquery())
    earlier = aliased(ProductStock)
    had_before = (select(func.count()).where(earlier.product_id == s.product_id, earlier.snapshot_at < s.snapshot_at)
                  .correlate(s).scalar_subquery())
    after_total = _total_present(s.product_id, s.snapshot_at, strict=False)
    before_total = _total_present(s.product_id, s.snapshot_at, strict=True)
    # закончился: этот склад стал 0, а был больше, и после этого момента на всех складах 0;
    # снова в наличии: этот склад стал больше 0 (был 0 или его не было), до этого момента на всех складах было 0
    out_cond = and_(s.present <= 0, func.coalesce(prev_present, 0) > 0, after_total <= 0)
    in_cond = and_(s.present > 0, func.coalesce(prev_present, 0) <= 0, before_total <= 0, had_before > 0)
    q = (select(s, Product, after_total.label("total"))
         .join(Product, Product.id == s.product_id)
         .where(Product.user_id == c.store_id, c.of_products(s.product_id), c.upto(s.snapshot_at),
                or_(out_cond, in_cond))
         .order_by(s.snapshot_at.desc()).limit(c.k))
    rows = (await db.execute(q)).all()
    out, seen = [], set()
    for st, p, total in rows:
        at = ef.utc(st.snapshot_at)
        kind = "restock" if st.present > 0 else "stockout"
        if (p.id, at, kind) in seen:  # несколько складов в один момент — одно событие
            continue
        seen.add((p.id, at, kind))
        out.append(ef.restock(f"restock:{st.id}", at, _ref(p), int(total)) if kind == "restock"
                   else ef.stockout(f"stockout:{st.id}", at, _ref(p)))
    return out, len(rows) == c.k


# ── аномалии ──
async def _anomalies(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    today = today_msk()
    out, full = [], False
    for column in (Anomaly.detected_at, Anomaly.resolved_at):
        q = (select(Anomaly).where(Anomaly.user_id == c.store_id, column.is_not(None), c.upto(column),
                                   c.of_products(Anomaly.product_id))
             .order_by(column.desc()).limit(c.k))
        if column is Anomaly.resolved_at:
            q = q.where(or_(Anomaly.resolution.is_(None), Anomaly.resolution != ESCALATED))
        rows = list((await db.execute(q)).scalars())
        full = full or len(rows) == c.k
        catalog = await _catalog(db, [a.product_id for a in rows])
        for a in rows:
            p = catalog.get(a.product_id)
            text = describe(a.type, a.severity, a.metric_before, a.metric_after, a.threshold, a.details, a.product_id,
                            (p.name or p.offer_id) if p else None, today)
            ref = _ref(p) if p else None
            type_title = TYPE_TITLES.get(a.type, a.type)
            if column is Anomaly.detected_at:
                out.append(ef.anomaly_found(f"anomaly:{a.id}", ef.utc(a.detected_at), ref, type_title, text.title,
                                            a.severity == RED, text.link))
            else:
                out.append(ef.anomaly_resolved(f"resolved:{a.id}", ef.utc(a.resolved_at), ref, type_title, a.resolution,
                                               text.link))
    return out, full


# ── действия и ошибки загрузки ──
async def _actions(db: AsyncSession, c: Ctx, errors: bool) -> tuple[list[ef.Event], bool]:
    """Действия пользователя (успешные запросы) или — errors — не загрузившиеся файлы отчётов."""
    if errors and c.products is not None:
        return [], False
    q = select(AuditLog).where(AuditLog.user_id == c.store_id, AuditLog.entity_type == HTTP_ENTITY,
                               c.upto(AuditLog.created_at))
    if errors:
        q = q.where(AuditLog.method == ef.REPORT_UPLOAD[0], AuditLog.entity_id == ef.REPORT_UPLOAD[1],
                    AuditLog.status != OK)
    elif c.products is not None:  # по товару — правка его себестоимости
        q = q.where(AuditLog.status == OK, AuditLog.entity_id == ef.COSTS_TEMPLATE,
                    AuditLog.path.in_([ef.costs_path(p.offer_id) for p in c.products]))
    else:
        q = q.where(AuditLog.status == OK, AuditLog.entity_id.in_({t for _, t in ef.ACTIONS}))
    rows = list((await db.execute(q.order_by(AuditLog.created_at.desc()).limit(c.k))).scalars())
    out = []
    for r in rows:
        if errors:
            out.append(ef.upload_failed(f"upload:{r.id}", ef.utc(r.created_at)))
        elif (e := ef.action(f"action:{r.id}", ef.utc(r.created_at), r.method or "", r.entity_id, r.path)) is not None:
            out.append(e)
    return out, len(rows) == c.k


def _report_name(r: Report) -> str:
    return f"за {r.period}" if r.period else f"«{r.filename}»"


async def _reports(db: AsyncSession, c: Ctx, errors: bool) -> tuple[list[ef.Event], bool]:
    if c.products is not None:
        return [], False
    column = Report.parsed_at if errors else Report.uploaded_at
    q = select(Report).where(Report.user_id == c.store_id, c.upto(column))
    if errors:
        q = q.where(Report.status == REPORT_ERROR)
    rows = list((await db.execute(q.order_by(column.desc()).limit(c.k))).scalars())
    if errors:
        out = [ef.report_error(f"report-error:{r.id}", ef.utc(r.parsed_at), str(r.id), _report_name(r), r.error_message)
               for r in rows]
    else:
        out = [ef.report_uploaded(f"report:{r.id}", ef.utc(r.uploaded_at), str(r.id), _report_name(r),
                                  r.report_type == "accrual", r.source == "api") for r in rows]
    return out, len(rows) == c.k


async def _sync_failures(db: AsyncSession, c: Ctx) -> tuple[list[ef.Event], bool]:
    """Начало сбоя: запуск с ошибкой, а прошлый запуск того же вида — нет (или его не было). Повторы — не события."""
    if c.products is not None:
        return [], False
    s, prev = SyncRun, aliased(SyncRun)
    prev_status = (select(prev.status).where(prev.user_id == s.user_id, prev.kind == s.kind,
                                             prev.started_at < s.started_at)
                   .order_by(prev.started_at.desc()).limit(1).correlate(s).scalar_subquery())
    q = (select(s).where(s.user_id == c.store_id, s.status == FAILED, c.upto(s.started_at),
                         or_(prev_status.is_(None), prev_status != FAILED))
         .order_by(s.started_at.desc()).limit(c.k))
    rows = list((await db.execute(q)).scalars())
    out = []
    for r in rows:
        problem = overview.PROBLEMS[overview.classify(r.kind, r)]
        label, link = problem.action or ("Синхронизация", SYNC_WINDOW)
        out.append(ef.sync_failed(f"sync:{r.id}", ef.utc(r.started_at), overview.TITLES.get(r.kind, r.kind),
                                  problem.text, problem.hint, link, label))
    return out, len(rows) == c.k


SOURCES = {
    ef.PRICE: (_price_changes, _price_batches, _price_cabinet),
    ef.PROMO: (_promotions,),
    ef.STOCK: (_stock,),
    ef.ANOMALY: (_anomalies,),
    ef.ACTION: (lambda db, c: _actions(db, c, False), lambda db, c: _reports(db, c, False)),
    ef.ERROR: (_sync_failures, lambda db, c: _reports(db, c, True), lambda db, c: _actions(db, c, True)),
}


def _start(d: date) -> datetime:
    return datetime.combine(d, time(), MSK)


async def feed(db: AsyncSession, store_id: uuid.UUID, *, date_from: date | None = None, date_to: date | None = None,
               kinds: list[str] | None = None, product_ids: list[uuid.UUID] | None = None,
               category_id: int | None = None, before: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    """Страница ленты: события до курсора по убыванию времени и курсор следующей страницы."""
    if date_from and date_to and date_from > date_to:
        raise AnalyticsError("Дата начала позже даты конца периода")
    cursor = None
    if before:
        if (cursor := ef.decode_cursor(before)) is None:
            raise AnalyticsError("Не удалось продолжить ленту — обновите страницу")
    kinds = [k for k in (kinds or ef.KINDS) if k in SOURCES]
    products = await _products(db, store_id, sorted(set(product_ids or []), key=str), category_id)
    limit = max(1, min(limit, MAX_LIMIT))
    since = _start(date_from) if date_from else None
    until = _start(date_to + timedelta(days=1)) - timedelta(microseconds=1) if date_to else None
    ctx = Ctx(store_id, products, cursor[0] if cursor else None, since, until, limit * 2)
    results = []
    if products is None or products:  # фильтр без товаров (пустая категория) — событий нет
        for kind in kinds:
            for source in SOURCES[kind]:
                results.append(await source(db, ctx))
    p = ef.page(results, limit, cursor, since)
    return {
        "items": [{
            "key": e.key, "at": e.at.isoformat(), "kind": e.kind, "kind_title": ef.KIND_TITLES[e.kind], "title": e.title,
            "detail": e.detail, "tone": e.tone,
            "product": None if e.product is None else {"id": e.product.id, "offer_id": e.product.offer_id,
                                                       "name": e.product.name},
            "link": e.link, "link_label": e.link_label,
        } for e in p.events],
        "next": p.next,
        "kinds": [{"kind": k, "title": ef.KIND_TITLES[k]} for k in ef.KINDS],
    }
