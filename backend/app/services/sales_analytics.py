"""«Аналитика продаж» (шаг 10.8): продажи по товарам за период, события, которые влияли на продажи, выгрузка в Excel.

Показатели и графики — общие `/analytics/metrics` (10.3); здесь — то, чего там нет:
- таблица по товарам: заказы (отправления без отмен, 5.2), продано и возвращено, шт., выручка и возвраты, ₽
  (`sales_daily`, только дни с начислениями — остальные неизвестны, не 0), доля возвратов, средняя цена, продажи к
  прошлому сопоставимому периоду с поправкой на дни с данными и значимостью, как в «Обзоре» (`engine/leaders.compare_qty`);
- события по дням (`engine/sales_events`): цена, вход и выход из акции, «нет остатка» и «снова в наличии».
Фильтр товаров и категории — как у показателей (`analytics._products`). Ответы кэшируются вместе с P&L (5 минут).
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import leaders as lead
from app.engine import sales_events as ev
from app.models.posting import Posting, PostingItem, PostingSync
from app.models.price_change import APPLIED, UNKNOWN, PriceChange
from app.models.product import Product, ProductPrice
from app.models.promotion import Promotion, PromotionProduct
from app.models.sales_daily import SalesDaily
from app.models.warehouse import ProductStock, Warehouse
from app.schemas.sync import utc
from app.services import pnl_data, pnl_service
from app.services.analytics import MSK, AnalyticsError, _out, _products
from app.services.dashboard import previous_period
from app.services.pnl_cache import cache
from app.services.sync.postings import is_cancelled
from app.services.xlsx_export import Col, table_xlsx

SORTS = ("revenue", "sold_qty", "orders", "returned_qty", "return_share", "avg_price", "change_pct", "offer_id")


def _check(date_from: date, date_to: date) -> None:
    try:
        pnl_service.PnLRequest(date_from, date_to).check()
    except pnl_service.PnLRequestError as exc:
        raise AnalyticsError(str(exc)) from None


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Период по Москве — в моменты UTC: [начало первого дня, начало дня после последнего)."""
    start = datetime.combine(date_from, time(), MSK).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time(), MSK).astimezone(timezone.utc)
    return start, end


def _day(dt: datetime) -> date:
    return utc(dt).astimezone(MSK).date()


# ── таблица по товарам ──
async def _sold(db: AsyncSession, store_id: uuid.UUID, days: set[date], offers: set[str] | None) -> dict[str, list]:
    """Артикул → [продано, возвращено, выручка, возвраты ₽] за дни с данными."""
    if not days:
        return {}
    q = (select(SalesDaily.offer_id, func.sum(SalesDaily.qty_sold), func.sum(SalesDaily.qty_returned),
                func.sum(SalesDaily.revenue), func.sum(SalesDaily.returns_amount))
         .where(SalesDaily.user_id == store_id, SalesDaily.day.in_(days)).group_by(SalesDaily.offer_id))
    if offers is not None:
        q = q.where(SalesDaily.offer_id.in_(offers))
    return {o: [int(s or 0), int(r or 0), Decimal(v or 0), Decimal(a or 0)] for o, s, r, v, a in (await db.execute(q)).all()}


async def _orders(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                  offers: set[str] | None) -> tuple[dict[str, list[int]], int]:
    """Артикул → [заказы, штук в заказах] без отмен; и сколько дней периода покрыто загруженными отправлениями."""
    syncs = list((await db.execute(select(PostingSync).where(PostingSync.user_id == store_id))).scalars())
    covered = {
        d for s in syncs
        for d in pnl_data.days_between(max(s.loaded_from, date_from), min(_day(s.synced_at), date_to))
    }
    out: dict[str, list] = {}
    q = (select(Posting.id, Posting.day, Posting.status, PostingItem.offer_id, PostingItem.quantity)
         .join(PostingItem, PostingItem.posting_id == Posting.id)
         .where(Posting.user_id == store_id, Posting.day >= date_from, Posting.day <= date_to))
    if offers is not None:
        q = q.where(PostingItem.offer_id.in_(offers))
    for posting_id, day, status, offer, qty in (await db.execute(q)).all():
        if offer is None or is_cancelled(status) or day not in covered:
            continue
        acc = out.setdefault(offer, [set(), 0])
        acc[0].add(posting_id)
        acc[1] += qty
    return {o: [len(a[0]), a[1]] for o, a in out.items()}, len(covered)


def _share(part: int, whole: int) -> Decimal | None:
    return None if whole <= 0 else (Decimal(part) / whole * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


async def table(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                product_ids: list[uuid.UUID] | None = None, category_id: int | None = None) -> dict:
    """Продажи по товарам за период: заказы, продано, возвраты и их доля, выручка, средняя цена, к прошлому периоду."""
    _check(date_from, date_to)
    product_ids = sorted(set(product_ids or []), key=str)
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "products": [str(p) for p in product_ids],
              "category": category_id}
    if (hit := await cache.get(store_id, "sales-table", params)) is not None:
        return hit
    products = await _products(db, store_id, product_ids, category_id)
    offers = None if products is None else {p.offer_id for p in products}
    catalog = {p.offer_id: p for p in (await db.execute(select(Product).where(Product.user_id == store_id))).scalars()}

    prev_from, prev_to = previous_period(date_from, date_to)
    cov = await pnl_data.coverage(db, store_id, date_from, date_to, "auto")
    prev_cov = await pnl_data.coverage(db, store_id, prev_from, prev_to, "auto")
    days = {d for d, s in cov.days.items() if s is not None}
    prev_days = {d for d, s in prev_cov.days.items() if s is not None}
    now, prev = await _sold(db, store_id, days, offers), await _sold(db, store_id, prev_days, offers)
    orders, orders_days = await _orders(db, store_id, date_from, date_to, offers)

    keys = set(now) | {o for o, v in prev.items() if v[0] > 0} | set(orders)
    if offers is not None:
        keys |= offers  # выбранные товары — строкой даже без продаж
    items = []
    for offer in keys:
        sold, returned, revenue, returns = now.get(offer, [0, 0, Decimal(0), Decimal(0)])
        prev_sold = prev.get(offer, [0])[0]
        c = lead.compare_qty(sold, prev_sold, len(days), len(prev_days))
        o = orders.get(offer)
        p = catalog.get(offer)
        items.append({
            "product": {"id": str(p.id) if p else None, "offer_id": offer, "name": p.name if p else None},
            "orders": (o[0] if o else 0) if orders_days else None,
            "ordered_qty": (o[1] if o else 0) if orders_days else None,
            "sold_qty": sold if days else None, "returned_qty": returned if days else None,
            "return_share": _share(returned, sold), "revenue": revenue if days else None,
            "returns_amount": returns if days else None,
            "avg_price": (revenue / sold).quantize(Decimal("0.01"), ROUND_HALF_UP) if sold else None,
            "prev_sold_qty": prev_sold if prev_days else None,
            "expected": c.expected if c else None, "change_pct": c.change_pct if c else None,
            "direction": c.direction if c else None,
        })
    items.sort(key=lambda i: (-(i["revenue"] or 0), i["product"]["offer_id"]))
    totals = {k: sum((i[k] or 0) for i in items) for k in ("orders", "ordered_qty", "sold_qty", "returned_qty")}
    totals["revenue"] = sum((i["revenue"] or Decimal(0) for i in items), Decimal(0))
    totals["return_share"] = _share(totals["returned_qty"], totals["sold_qty"])
    out = _out({
        "date_from": date_from, "date_to": date_to, "previous": {"date_from": prev_from, "date_to": prev_to},
        "days": len(days), "prev_days": len(prev_days), "orders_days": orders_days,
        "compared": len(days) >= lead.MIN_DAYS and len(prev_days) >= lead.MIN_DAYS,
        "items": items, "totals": totals,
    })
    await cache.set(store_id, "sales-table", params, out)
    return out


# ── события ──
async def _price_raw(db: AsyncSession, store_id: uuid.UUID, start: datetime, end: datetime,
                     refs: dict[uuid.UUID, ev.ProductRef]) -> list[ev.Raw]:
    ids = list(refs)
    before = (select(ProductPrice.product_id, func.max(ProductPrice.snapshot_at).label("mx"))
              .where(ProductPrice.product_id.in_(ids), ProductPrice.snapshot_at < start)
              .group_by(ProductPrice.product_id).subquery())
    start_price = {s.product_id: s.price for s in (await db.execute(select(ProductPrice).join(
        before, and_(ProductPrice.product_id == before.c.product_id, ProductPrice.snapshot_at == before.c.mx)))).scalars()}
    series: dict[uuid.UUID, list] = {}
    for s in (await db.execute(select(ProductPrice).where(
        ProductPrice.product_id.in_(ids), ProductPrice.snapshot_at >= start, ProductPrice.snapshot_at < end,
    ).order_by(ProductPrice.snapshot_at))).scalars():
        series.setdefault(s.product_id, []).append((_day(s.snapshot_at), s.price))
    service: set[tuple[uuid.UUID, date, Decimal]] = {
        (pid, _day(at), Decimal(price)) for pid, at, price in await db.execute(
            select(PriceChange.product_id, PriceChange.created_at, PriceChange.price_after).where(
                PriceChange.user_id == store_id, PriceChange.product_id.in_(ids),
                PriceChange.status.in_((APPLIED, UNKNOWN)), PriceChange.price_after.is_not(None),
                PriceChange.created_at >= start - timedelta(days=1), PriceChange.created_at < end))
    }
    return [ev.price_raw(day, refs[pid], was, now, (pid, day, now) in service)
            for pid, points in series.items() for day, was, now in ev.price_changes(start_price.get(pid), points)]


async def _promo_raw(db: AsyncSession, store_id: uuid.UUID, start: datetime, end: datetime,
                     refs: dict[uuid.UUID, ev.ProductRef]) -> list[ev.Raw]:
    out = []
    for r, promo in await db.execute(
        select(PromotionProduct, Promotion).join(Promotion, Promotion.id == PromotionProduct.promotion_id).where(
            Promotion.user_id == store_id, PromotionProduct.product_id.in_(list(refs)),
            PromotionProduct.is_active.is_(True),
            or_(and_(PromotionProduct.first_seen_at >= start, PromotionProduct.first_seen_at < end),
                and_(PromotionProduct.left_at >= start, PromotionProduct.left_at < end)))
    ):
        ref = refs[r.product_id]
        if start <= utc(r.first_seen_at) < end:
            out.append(ev.promo_raw(_day(r.first_seen_at), ev.PROMO_IN, ref, promo.id, promo.title, r.added_automatically))
        if r.left_at is not None and start <= utc(r.left_at) < end:
            out.append(ev.promo_raw(_day(r.left_at), ev.PROMO_OUT, ref, promo.id, promo.title, False))
    return out


async def _stock_raw(db: AsyncSession, store_id: uuid.UUID, start: datetime, end: datetime,
                     refs: dict[uuid.UUID, ev.ProductRef]) -> list[ev.Raw]:
    ids = list(refs)
    base = (select(ProductStock.product_id, ProductStock.warehouse_id, func.max(ProductStock.snapshot_at).label("mx"))
            .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
            .where(Warehouse.user_id == store_id, ProductStock.product_id.in_(ids), ProductStock.snapshot_at < start)
            .group_by(ProductStock.product_id, ProductStock.warehouse_id).subquery())
    start_stock: dict[uuid.UUID, dict[str, int]] = {}
    for s in (await db.execute(select(ProductStock).join(base, and_(
        ProductStock.product_id == base.c.product_id, ProductStock.warehouse_id == base.c.warehouse_id,
        ProductStock.snapshot_at == base.c.mx)))).scalars():
        start_stock.setdefault(s.product_id, {})[str(s.warehouse_id)] = max(s.present, 0)
    series: dict[uuid.UUID, list] = {}
    for s in (await db.execute(select(ProductStock).join(Warehouse, Warehouse.id == ProductStock.warehouse_id).where(
        Warehouse.user_id == store_id, ProductStock.product_id.in_(ids),
        ProductStock.snapshot_at >= start, ProductStock.snapshot_at < end,
    ).order_by(ProductStock.snapshot_at))).scalars():
        series.setdefault(s.product_id, []).append((_day(s.snapshot_at), str(s.warehouse_id), s.present))
    return [ev.stock_raw(day, kind, refs[pid])
            for pid, points in series.items() for day, kind in ev.stock_changes(start_stock.get(pid, {}), points)]


async def events(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                 product_ids: list[uuid.UUID] | None = None, category_id: int | None = None) -> list[dict]:
    """События периода по дням: цена, вход и выход из акции, «нет остатка», «снова в наличии»."""
    _check(date_from, date_to)
    product_ids = sorted(set(product_ids or []), key=str)
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "products": [str(p) for p in product_ids],
              "category": category_id}
    if (hit := await cache.get(store_id, "sales-events", params)) is not None:
        return hit
    products = await _products(db, store_id, product_ids, category_id)
    if products is None:
        products = list((await db.execute(select(Product).where(Product.user_id == store_id))).scalars())
    refs = {p.id: ev.ProductRef(p.id, p.offer_id, p.name) for p in products}
    start, end = _bounds(date_from, date_to)
    raw = [] if not refs else [
        *await _price_raw(db, store_id, start, end, refs), *await _promo_raw(db, store_id, start, end, refs),
        *await _stock_raw(db, store_id, start, end, refs),
    ]
    out = _out([
        {"day": e.day, "kind": e.kind, "title": e.title, "count": e.count, "link": e.link,
         "items": [{"product": {"id": str(r.product.id), "offer_id": r.product.offer_id, "name": r.product.name},
                    "text": r.text, "link": r.link} for r in e.items]}
        for e in ev.by_day(raw)
    ])
    await cache.set(store_id, "sales-events", params, out)
    return out


# ── Excel ──
COLUMNS = [
    Col("Артикул", 24), Col("Название", 40), Col("Заказы", 10, "int"), Col("Заказано, шт.", 11, "int"),
    Col("Продано, шт.", 11, "int"), Col("Возвраты, шт.", 11, "int"), Col("Доля возвратов, %", 11, "number"),
    Col("Выручка, ₽", 14, "money"), Col("Возвраты, ₽", 12, "money"), Col("Средняя цена, ₽", 12, "money"),
    Col("Продано в прошлом периоде, шт.", 14, "int"), Col("К прошлому периоду, %", 12, "number"),
]


def to_xlsx(data: dict, filter_text: str) -> bytes:
    def num(v):
        return None if v is None else Decimal(v)

    rows = [[i["product"]["offer_id"], i["product"]["name"] or "", i["orders"], i["ordered_qty"], i["sold_qty"],
             i["returned_qty"], num(i["return_share"]), num(i["revenue"]), num(i["returns_amount"]), num(i["avg_price"]),
             i["prev_sold_qty"], num(i["change_pct"])] for i in data["items"]]
    t = data["totals"]
    fmt = lambda iso: date.fromisoformat(iso).strftime("%d.%m.%Y")  # noqa: E731
    return table_xlsx(
        "Продажи по товарам", COLUMNS, rows,
        total=["Итого", "", t["orders"], t["ordered_qty"], t["sold_qty"], t["returned_qty"], num(t["return_share"]),
               num(t["revenue"]), None, None, None, None],
        notes=[
            "Продажи по товарам",
            f"Период: {fmt(data['date_from'])} – {fmt(data['date_to'])}; прошлый период: "
            f"{fmt(data['previous']['date_from'])} – {fmt(data['previous']['date_to'])}.",
            f"Товары: {filter_text}.",
            f"Дней с данными о продажах: {data['days']} (в прошлом периоде — {data['prev_days']}); дней с загруженными "
            f"отправлениями: {data['orders_days']}. Дни без данных — пропуски, а не ноль.",
            "Заказы — отправления по дню заказа без отмен; продажи и возвраты — по начислениям Ozon (продажа — когда Ozon "
            "начислил выручку), поэтому заказы и продажи за один период не совпадают.",
            "К прошлому периоду — продано сейчас против прошлого периода с поправкой на дни с данными.",
        ],
    )
