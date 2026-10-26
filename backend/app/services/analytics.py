"""Основа аналитики (шаг 10.3): одни и те же показатели для главной, «Аналитики», «Товаров» и «Финансов»; лучшие и
худшие товары «Обзора бизнеса» (шаг 10.7, `leaders`).

Определения — BUSINESS_LOGIC §10 «Показатели», ряды и итоги — engine/metrics. Откуда данные (по текущему магазину):
- выручка и прибыль — из P&L, не новым расчётом. Магазин целиком: по дням — `pnl_service.get_by_day` (прибыль до налога),
  итоги — `pnl_service.get_pnl` (как на главной 3.7: остаток после налога, если налог есть в обоих периодах, иначе до
  налога). Товары или категория: тот же движок, что «Прибыль по товарам» (`product_pnl.runner`), по дням и за период —
  итоги совпадают с этой страницей. Прибыль товара — до налога, маржа — по товарам с себестоимостью;
- продажи и возвраты, шт. и ₽ — `sales_daily` (шаг 3.5); день без начислений — нет данных, а не 0;
- заказы — отправления (шаг 5.2) по дню создания без отмен; день вне загруженных отправлений — нет данных.
Прошлый сопоставимый период — `dashboard.previous_period`. Ответ кэшируется на 5 минут вместе с P&L (pnl_cache):
новые начисления, отчёты, себестоимость и налог сбрасывают его сразу, новые заказы видны не позже чем через 5 минут.
"""
import uuid
from datetime import date, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import leaders as lead
from app.engine import metrics as m
from app.models.ozon_category import OzonCategory
from app.models.posting import Posting, PostingItem, PostingSync
from app.models.product import Product
from app.models.sales_daily import SalesDaily
from app.services import pnl_data, pnl_service, product_pnl
from app.services.dashboard import previous_period
from app.services.pnl_cache import cache
from app.services.sync.postings import is_cancelled

MSK = ZoneInfo("Europe/Moscow")
NO_CATEGORY = 0  # «Без категории» в фильтре
NO_CATEGORY_NAME = "Без категории"
MAX_PRODUCTS = 50  # товаров в одном фильтре


class AnalyticsError(Exception):
    """Запрос не подходит. Текст — для пользователя; code — HTTP-код."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.message, self.code = message, code


# ── фильтр ──
async def _products(db: AsyncSession, store_id: uuid.UUID, product_ids: list[uuid.UUID],
                    category_id: int | None) -> list[Product] | None:
    """Товары фильтра; None — магазин целиком. Чужой или несуществующий товар — 404."""
    if not product_ids and category_id is None:
        return None
    if len(product_ids) > MAX_PRODUCTS:
        raise AnalyticsError(f"Не больше {MAX_PRODUCTS} товаров за раз — выберите категорию")
    q = select(Product).where(Product.user_id == store_id)
    if product_ids:
        q = q.where(Product.id.in_(product_ids))
    rows = list((await db.execute(q.order_by(Product.offer_id))).scalars())
    if product_ids and len(rows) != len(product_ids):
        raise AnalyticsError("Товар не найден — возможно, он из другого магазина", 404)
    if category_id is not None:  # товары и категория вместе — товары этой категории
        rows = [p for p in rows if (p.category_id or NO_CATEGORY) == category_id]
    return rows


async def _category(db: AsyncSession, category_id: int | None) -> dict | None:
    if category_id is None:
        return None
    if category_id == NO_CATEGORY:
        return {"id": NO_CATEGORY, "name": NO_CATEGORY_NAME, "path": NO_CATEGORY_NAME}
    c = await db.get(OzonCategory, category_id)
    return {"id": category_id, "name": c.name if c else f"Категория {category_id}", "path": c.path if c else None}


# ── данные по дням ──
async def _orders(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                  product_ids: set[uuid.UUID] | None) -> dict[date, tuple[int, int] | None]:
    """День → (заказы, штуки); None — отправления за этот день не загружены."""
    syncs = list((await db.execute(select(PostingSync).where(PostingSync.user_id == store_id))).scalars())

    def covered(day: date) -> bool:
        for s in syncs:
            synced = s.synced_at if s.synced_at.tzinfo else s.synced_at.replace(tzinfo=timezone.utc)
            if s.loaded_from <= day <= synced.astimezone(MSK).date():
                return True
        return False

    counts: dict[date, list] = {}
    q = (select(Posting.id, Posting.day, Posting.status, PostingItem.product_id, PostingItem.quantity)
         .join(PostingItem, PostingItem.posting_id == Posting.id)
         .where(Posting.user_id == store_id, Posting.day >= date_from, Posting.day <= date_to))
    if product_ids is not None:
        q = q.where(PostingItem.product_id.in_(product_ids))
    for pid, day, status, _product, qty in (await db.execute(q)).all():
        if is_cancelled(status):
            continue
        acc = counts.setdefault(day, [set(), 0])
        acc[0].add(pid)
        acc[1] += qty
    out: dict[date, tuple[int, int] | None] = {}
    d = date_from
    while d <= date_to:
        acc = counts.get(d)
        out[d] = (len(acc[0]), acc[1]) if acc else ((0, 0) if covered(d) else None)
        d += timedelta(days=1)
    return out


async def _sales(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                 offers: set[str] | None) -> dict[date, tuple[int, int, Decimal]]:
    """День → (продано, возвращено, сумма возвратов) по sales_daily; дней без продаж в словаре нет."""
    q = (select(SalesDaily.day, func.sum(SalesDaily.qty_sold), func.sum(SalesDaily.qty_returned),
                func.sum(SalesDaily.returns_amount))
         .where(SalesDaily.user_id == store_id, SalesDaily.day >= date_from, SalesDaily.day <= date_to)
         .group_by(SalesDaily.day))
    if offers is not None:
        q = q.where(SalesDaily.offer_id.in_(offers))
    return {day: (int(s or 0), int(r or 0), Decimal(a or 0)) for day, s, r, a in (await db.execute(q)).all()}


def _selected(result, offers: set[str]) -> tuple[Decimal, Decimal | None, Decimal]:
    """Выручка, прибыль (по товарам с себестоимостью; ни у одного нет — None) и выручка этих товаров — база маржи."""
    rows = [r for r in result.rows if r.offer_id in offers]
    with_cost = [r for r in rows if r.profit is not None]
    revenue = sum((r.revenue for r in rows), Decimal(0))
    profit = sum((r.profit for r in with_cost), Decimal(0)) if with_cost or not rows else None
    return revenue, profit, sum((r.revenue for r in with_cost), Decimal(0))


async def _days(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                products: list[Product] | None) -> tuple[dict[date, m.DayData], pnl_data.Coverage]:
    cov = await pnl_data.coverage(db, store_id, date_from, date_to, "auto")
    offers = None if products is None else {p.offer_id for p in products}
    days = {day: m.DayData() for day in cov.days}
    if products is None:
        by_day = await pnl_service.get_by_day(db, store_id, pnl_service.PnLRequest(date_from, date_to))
        for row in by_day["days"]:
            if row["revenue"] is None:
                continue
            d = days[date.fromisoformat(row["date"])]
            d.revenue, d.profit = Decimal(row["revenue"]), Decimal(row["profit_before_tax"])
            d.margin_base = d.revenue
    else:
        lines = await pnl_data.load_lines(db, store_id, cov)
        run = await product_pnl.runner(db, store_id, pnl_service.PnLRequest(date_from, date_to), lines)
        by_day: dict[date, list] = {}
        for ln in lines:
            by_day.setdefault(ln.day, []).append(ln)
        for day, source in cov.days.items():
            if source is None:
                continue
            d = days[day]
            d.revenue, d.profit, d.margin_base = _selected(run(by_day.get(day, [])), offers)
    sales = await _sales(db, store_id, date_from, date_to, offers)
    for day, source in cov.days.items():
        if source is None:
            continue
        sold, returned, amount = sales.get(day, (0, 0, Decimal(0)))
        d = days[day]
        d.sold_qty, d.returned_qty, d.returns_amount = sold, returned, amount
    product_ids = None if products is None else {p.id for p in products}
    for day, value in (await _orders(db, store_id, date_from, date_to, product_ids)).items():
        if value is not None:
            days[day].orders, days[day].ordered_qty = value
    return days, cov


async def _totals(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                  products: list[Product] | None, days: dict[date, m.DayData]) -> tuple[dict, dict | None]:
    """Итоги периода: штуки и заказы — суммой дней, выручка и прибыль — из P&L (магазин) или «Прибыли по товарам»."""
    totals = m.summarize(list(days.values()))
    pnl = None
    if products is None:
        pnl = await pnl_service.get_pnl(db, store_id, pnl_service.PnLRequest(date_from, date_to))
        if pnl["revenue_base"] is not None:
            totals["revenue"] = Decimal(pnl["revenue_base"])
        totals["missing_cost"] = len(pnl["missing_cost"])
    else:
        value = await product_pnl.compute(db, store_id, pnl_service.PnLRequest(date_from, date_to))
        offers = {p.offer_id for p in products}
        rows = [r for r in value["items"] if r["offer_id"] in offers]
        if value["data_source"] != "none":
            with_cost = [r for r in rows if r["profit"] is not None]
            totals["revenue"] = sum((Decimal(r["revenue"]) for r in rows), Decimal(0))
            totals["profit"] = sum((Decimal(r["profit"]) for r in with_cost), Decimal(0)) if with_cost or not rows else None
            totals["margin_base"] = sum((Decimal(r["revenue"]) for r in with_cost), Decimal(0))
            totals["missing_cost"] = len(rows) - len(with_cost)
    return totals, pnl


def _profit_from_pnl(totals: dict, pnl: dict | None, net: bool) -> None:
    """Прибыль магазина — как на главной: остаток после налога (net) или до налога; маржа = прибыль / выручка."""
    p = pnl and pnl["pnl"]
    if not p:
        return
    totals["profit"] = Decimal(p["net"] if net else p["profit_before_tax"])
    totals["margin_base"] = totals["revenue"]


def _finish(totals: dict) -> dict:
    """Маржа и средняя цена — заново, от итоговых выручки и прибыли (их подставили P&L или «Прибыль по товарам»)."""
    t = dict(totals)
    t["margin"] = (t["profit"] / t["margin_base"] * 100).quantize(m.CENT) if t["profit"] is not None and t.get(
        "margin_base") else None
    t["avg_price"] = (t["revenue"] / t["sold_qty"]).quantize(m.CENT) if t["revenue"] is not None and t["sold_qty"] else None
    return t


TOTAL_KEYS = (*m.VALUES, "missing_days", "orders_missing_days", "missing_cost")


def _out(v):
    if isinstance(v, Decimal):
        return str(v.quantize(m.CENT))
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _out(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_out(x) for x in v]
    return v


def _coverage(cov: pnl_data.Coverage, days: dict[date, m.DayData]) -> dict:
    return {
        **pnl_service.coverage_out(cov),
        "orders_missing_days": sum(d.orders is None for d in days.values()),
        "data_source": cov.data_source,
    }


async def metrics(
    db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date, granularity: str = "day",
    product_ids: list[uuid.UUID] | None = None, category_id: int | None = None, compare: bool = False,
) -> dict:
    if granularity not in m.GRANULARITIES:
        raise AnalyticsError("Детализация: день, неделя или месяц")
    try:
        pnl_service.PnLRequest(date_from, date_to).check()
    except pnl_service.PnLRequestError as exc:
        raise AnalyticsError(str(exc)) from None
    product_ids = sorted(set(product_ids or []), key=str)
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "g": granularity,
              "products": [str(p) for p in product_ids], "category": category_id, "compare": compare}
    if (hit := await cache.get(store_id, "metrics", params)) is not None:
        return hit

    products = await _products(db, store_id, product_ids, category_id)
    days, cov = await _days(db, store_id, date_from, date_to, products)
    totals, pnl = await _totals(db, store_id, date_from, date_to, products, days)
    points = m.series(days, date_from, date_to, granularity)

    prev = None
    if compare:
        prev_from, prev_to = previous_period(date_from, date_to)
        prev_days, prev_cov = await _days(db, store_id, prev_from, prev_to, products)
        prev_totals, prev_pnl = await _totals(db, store_id, prev_from, prev_to, products, prev_days)
        points = m.with_previous(points, m.series(prev_days, prev_from, prev_to, granularity))
        prev = {"date_from": prev_from, "date_to": prev_to, "coverage": _coverage(prev_cov, prev_days),
                "pnl": prev_pnl, "totals": prev_totals}

    # прибыль магазина сравниваем одинаковую: после налога, если налог есть в обоих периодах (как на главной 3.7)
    profit_kind = "before_tax"
    if products is None and pnl and pnl["pnl"]:
        has_net = pnl["pnl"]["net"] is not None
        prev_net = prev is None or not prev["pnl"] or not prev["pnl"]["pnl"] or prev["pnl"]["pnl"]["net"] is not None
        profit_kind = "net" if has_net and prev_net else "before_tax"
        _profit_from_pnl(totals, pnl, profit_kind == "net")
        if prev is not None:
            _profit_from_pnl(prev["totals"], prev["pnl"], profit_kind == "net")
    totals = _finish(totals)

    value = {
        "date_from": date_from, "date_to": date_to, "granularity": granularity,
        "filter": {
            "products": [{"id": str(p.id), "offer_id": p.offer_id, "name": p.name} for p in products]
            if product_ids and products is not None else None,
            "category": await _category(db, category_id),
            "product_count": None if products is None else len(products),
        },
        "coverage": _coverage(cov, days),
        "profit_kind": profit_kind,  # итоговая прибыль: net — после налога, before_tax — до налога; в ряде — до налога
        "totals": {k: totals.get(k, 0 if k == "missing_cost" else None) for k in TOTAL_KEYS},
        "points": points,
        "previous": None,
        "changes": None,
    }
    if prev is not None:
        prev_totals = _finish(prev["totals"])
        value["previous"] = {
            "date_from": prev["date_from"], "date_to": prev["date_to"], "coverage": prev["coverage"],
            "totals": {k: prev_totals.get(k, 0 if k == "missing_cost" else None) for k in TOTAL_KEYS},
        }
        value["changes"] = {k: None if v is None else str(v) for k, v in m.changes(totals, prev_totals).items()}
        value["compare_note"] = (
            "За прошлый период данных нет — сравнивать не с чем." if prev["coverage"]["data_source"] == "none"
            else "В одном из периодов есть дни без данных — сравнение приблизительное."
            if cov.count(None) or prev["coverage"]["missing_days"] else None
        )
    value = _out(value)
    await cache.set(store_id, "metrics", params, value)
    return value


# ── «Обзор бизнеса»: лучшие и худшие товары (шаг 10.7) ──
def _ref(offer: str, catalog: dict[str, Product]) -> dict:
    p = catalog.get(offer)
    return {"id": str(p.id) if p else None, "offer_id": offer, "name": p.name if p else None}


async def _sold(db: AsyncSession, store_id: uuid.UUID, days: set[date], offers: set[str] | None) -> dict[str, int]:
    """Продано, шт., по артикулам за дни с данными."""
    if not days:
        return {}
    q = (select(SalesDaily.offer_id, func.sum(SalesDaily.qty_sold))
         .where(SalesDaily.user_id == store_id, SalesDaily.day.in_(days)).group_by(SalesDaily.offer_id))
    if offers is not None:
        q = q.where(SalesDaily.offer_id.in_(offers))
    return {offer: int(n or 0) for offer, n in (await db.execute(q)).all() if n}


async def leaders(
    db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
    product_ids: list[uuid.UUID] | None = None, category_id: int | None = None,
) -> dict:
    """Лучшие и худшие товары периода по прибыли («Прибыль по товарам») и по изменению продаж к прошлому сопоставимому
    периоду — только значимые изменения (engine/leaders). Фильтр товаров и категории — как у показателей."""
    try:
        pnl_service.PnLRequest(date_from, date_to).check()
    except pnl_service.PnLRequestError as exc:
        raise AnalyticsError(str(exc)) from None
    product_ids = sorted(set(product_ids or []), key=str)
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "products": [str(p) for p in product_ids],
              "category": category_id}
    if (hit := await cache.get(store_id, "leaders", params)) is not None:
        return hit

    products = await _products(db, store_id, product_ids, category_id)
    offers = None if products is None else {p.offer_id for p in products}
    catalog = {p.offer_id: p for p in (await db.execute(select(Product).where(Product.user_id == store_id))).scalars()}

    value = await product_pnl.compute(db, store_id, pnl_service.PnLRequest(date_from, date_to))
    rows = [r for r in value["items"] if offers is None or r["offer_id"] in offers]
    profit_rows = [
        lead.ProfitRow(r["offer_id"], None, None, Decimal(r["revenue"]), None if r["profit"] is None else Decimal(r["profit"]),
                       None if r["margin"] is None else Decimal(r["margin"]), r["qty"])
        for r in rows
    ]
    best, worst = lead.by_profit(profit_rows)

    prev_from, prev_to = previous_period(date_from, date_to)
    cov = await pnl_data.coverage(db, store_id, date_from, date_to, "auto")
    prev_cov = await pnl_data.coverage(db, store_id, prev_from, prev_to, "auto")
    days = {d for d, s in cov.days.items() if s is not None}
    prev_days = {d for d, s in prev_cov.days.items() if s is not None}
    changes = lead.sales_changes(await _sold(db, store_id, days, offers), await _sold(db, store_id, prev_days, offers),
                                 len(days), len(prev_days))

    def profit_item(r: lead.ProfitRow) -> dict:
        return {"product": _ref(r.offer_id, catalog), "revenue": r.revenue, "profit": r.profit, "margin": r.margin,
                "qty": r.qty}

    def sales_item(c: lead.SalesChange) -> dict:
        return {"product": _ref(c.offer_id, catalog), "qty": c.qty, "prev_qty": c.prev_qty, "expected": c.expected,
                "change_pct": c.change_pct}

    note = None
    if changes is None:
        note = (f"Сравнивать продажи не с чем: нужно не меньше {lead.MIN_DAYS} дней с данными и в этом, и в прошлом "
                "периоде.")
    out = {
        "date_from": date_from, "date_to": date_to,
        "previous": {"date_from": prev_from, "date_to": prev_to},
        "profit": {
            "best": [profit_item(r) for r in best], "worst": [profit_item(r) for r in worst],
            "missing_cost": sum(1 for r in rows if r["profit"] is None), "data_source": value["data_source"],
        },
        "sales": {
            "compared": changes is not None, "note": note,
            "growth": [sales_item(c) for c in changes[0]] if changes else [],
            "decline": [sales_item(c) for c in changes[1]] if changes else [],
            "days": len(days), "prev_days": len(prev_days),
        },
    }
    out = _out(out)
    await cache.set(store_id, "leaders", params, out)
    return out


async def categories(db: AsyncSession, store_id: uuid.UUID) -> list[dict]:
    """Категории товаров магазина с числом товаров; без категории — «Без категории» (id 0). По названию."""
    res = await db.execute(
        select(Product.category_id, func.count(Product.id))
        .where(Product.user_id == store_id, Product.is_archived.is_(False)).group_by(Product.category_id)
    )
    counts = dict(res.all())
    ids = [c for c in counts if c is not None]
    names = {c.id: c for c in (await db.execute(select(OzonCategory).where(OzonCategory.id.in_(ids)))).scalars()}
    out = [
        {"id": cid, "name": names[cid].name if cid in names else f"Категория {cid}",
         "path": names[cid].path if cid in names else None, "products": counts[cid]}
        for cid in ids
    ]
    out.sort(key=lambda c: c["name"].lower())
    if None in counts:
        out.append({"id": NO_CATEGORY, "name": NO_CATEGORY_NAME, "path": None, "products": counts[None]})
    return out


async def search_products(db: AsyncSession, store_id: uuid.UUID, text: str | None, limit: int = 20,
                          ids: list[uuid.UUID] | None = None) -> list[dict]:
    """Товары для фильтра: по артикулу, названию или SKU (сначала не архивные) или по id — подписать выбранные."""
    q = select(Product).where(Product.user_id == store_id)
    if ids:
        q = q.where(Product.id.in_(ids))
    text = (text or "").strip()
    if text:
        like = f"%{text.lower()}%"
        cond = [func.lower(Product.offer_id).like(like), func.lower(Product.name).like(like)]
        if text.isdigit():
            cond.append(Product.ozon_sku == int(text))
        q = q.where(or_(*cond))
    rows = (await db.execute(q.order_by(Product.is_archived, Product.offer_id).limit(limit))).scalars()
    return [{"id": str(p.id), "offer_id": p.offer_id, "name": p.name, "category_id": p.category_id,
             "is_archived": p.is_archived} for p in rows]
