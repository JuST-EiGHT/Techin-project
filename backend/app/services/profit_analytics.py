"""«Аналитика прибыльности» (шаг 10.9): структура расходов, её разбор по статьям и товарам, исходные строки начислений.

Не новый расчёт — те же P&L (3.2) и «Прибыль по товарам» (3.4):
- магазин целиком — P&L периода: статьи начислений `by_type`, себестоимость `owed`, поправка «сколько пришло»
  (effective − auto); расходы = выручка − прибыль до налога P&L до копейки;
- товары или категория — строки «Прибыли по товарам» этих товаров: их статьи и себестоимость; начисления без товара
  («Не распределено») сюда не входят — они не относятся ни к одному товару;
- разбор группы — статьи группы и товары, на которые они пришлись (+ «Не распределено» у магазина целиком);
- строки товара — исходные начисления периода, отнесённые товару так же, как в «Прибыли по товарам»
  (`engine/product_pnl.attribute`): дата, статья, сумма, «ID начисления», источник — API или отчёт со ссылкой.
Таблица товаров — API «Прибыли по товарам» (`/pnl/by-product`, фильтры 10.9). Кэш — вместе с P&L.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import product_pnl as product_engine
from app.engine import profit_structure as ps
from app.models.product import Product
from app.models.report import Report
from app.services import pnl_data, pnl_service, product_pnl
from app.services.analytics import AnalyticsError, _out, _products
from app.services.dashboard import previous_period
from app.services.pnl_cache import cache

MAX_LINES = 500  # строк начислений товара за раз — больше обычно не бывает; иначе — «показаны первые»


def _check(date_from: date, date_to: date) -> pnl_service.PnLRequest:
    req = pnl_service.PnLRequest(date_from, date_to)
    try:
        req.check()
    except pnl_service.PnLRequestError as exc:
        raise AnalyticsError(str(exc)) from None
    return req


def _dec(v) -> Decimal:
    return Decimal(v) if v is not None else Decimal(0)


async def _structure_for(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                         offers: set[str] | None) -> tuple[ps.Structure, int, Decimal | None] | None:
    """Структура периода, сколько товаров без себестоимости и прибыль после налога (магазин, налог задан); данных нет —
    None."""
    req = pnl_service.PnLRequest(date_from, date_to)
    if offers is None:
        pnl = await pnl_service.get_pnl(db, store_id, req)
        if pnl["data_source"] == "none" or not pnl["pnl"]:
            return None
        p = pnl["pnl"]
        adjustment = _dec(p["effective_total"]) - _dec(pnl["auto_total"])
        s = ps.structure({k: Decimal(v) for k, v in pnl["by_type"].items()}, _dec(p["owed"]), adjustment)
        return s, len(pnl["missing_cost"]), None if p["net"] is None else Decimal(p["net"])
    value = await product_pnl.compute(db, store_id, req)
    if value["data_source"] == "none":
        return None
    rows = [r for r in value["items"] if r["offer_id"] in offers]
    by_type: dict[str, Decimal] = {}
    for r in rows:
        for name, amount in r["by_type"].items():
            by_type[name] = by_type.get(name, Decimal(0)) + Decimal(amount)
    known = [r for r in rows if r["cost"] is not None]
    cost = sum((Decimal(r["cost"]) for r in known), Decimal(0)) if known or not rows else None
    return ps.structure(by_type, cost), sum(1 for r in rows if r["cost_missing"]), None


def _groups(s: ps.Structure) -> list[dict]:
    return [{"key": g.key, "title": g.title, "amount": g.amount, "share": g.share(s.revenue),
             "types": [{"name": n, "amount": a} for n, a in g.types.items()]} for g in s.groups]


def _offers(products) -> set[str] | None:
    return None if products is None else {p.offer_id for p in products}


async def structure(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                    product_ids: list[uuid.UUID] | None = None, category_id: int | None = None) -> dict:
    """Выручка, расходы по группам, прибыль до налога и маржа за период и прошлый сопоставимый период."""
    _check(date_from, date_to)
    product_ids = sorted(set(product_ids or []), key=str)
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "products": [str(p) for p in product_ids],
              "category": category_id}
    if (hit := await cache.get(store_id, "profit-structure", params)) is not None:
        return hit
    offers = _offers(await _products(db, store_id, product_ids, category_id))
    prev_from, prev_to = previous_period(date_from, date_to)
    cur = await _structure_for(db, store_id, date_from, date_to, offers)
    prev = await _structure_for(db, store_id, prev_from, prev_to, offers)

    def totals(x: tuple[ps.Structure, int, Decimal | None] | None) -> dict | None:
        if x is None:
            return None
        s = x[0]
        margin = None if s.revenue <= 0 else (s.profit / s.revenue * 100)
        return {"revenue": s.revenue, "expenses": s.expenses, "profit": s.profit, "margin": margin}

    prev_groups = {g.key: g.amount for g in prev[0].groups} if prev else {}
    groups = _groups(cur[0]) if cur else []
    for g in groups:
        g["prev_amount"] = prev_groups.get(g["key"])
    out = _out({
        "date_from": date_from, "date_to": date_to, "scope": "store" if offers is None else "products",
        "has_data": cur is not None, **(totals(cur) or {"revenue": None, "expenses": None, "profit": None, "margin": None}),
        "missing_cost": cur[1] if cur else 0, "net": cur[2] if cur else None, "groups": groups,
        "previous": {"date_from": prev_from, "date_to": prev_to, **totals(prev)} if prev else None,
    })
    await cache.set(store_id, "profit-structure", params, out)
    return out


async def group_detail(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date, key: str,
                       product_ids: list[uuid.UUID] | None = None, category_id: int | None = None) -> dict:
    """Группа расходов → статьи и товары, на которые пришлись (больше расход — выше); у магазина — «Не распределено»."""
    if key not in ps.TITLES:
        raise AnalyticsError("Группа расходов: недопустимое значение")
    req = _check(date_from, date_to)
    offers = _offers(await _products(db, store_id, product_ids or [], category_id))
    value = await product_pnl.compute(db, store_id, req)
    rows = [r for r in value["items"] if offers is None or r["offer_id"] in offers]
    products = []
    for r in rows:
        if key == ps.COST:
            amount = None if r["cost"] is None else Decimal(r["cost"])
        else:
            amount = sum((-Decimal(a) for n, a in r["by_type"].items() if ps.group_of(n) == key), Decimal(0))
        if amount:
            products.append({"offer_id": r["offer_id"], "name": r["name"], "amount": amount, "revenue": Decimal(r["revenue"]),
                             "share": None if Decimal(r["revenue"]) <= 0 else amount / Decimal(r["revenue"]) * 100})
    products.sort(key=lambda x: (-x["amount"], x["offer_id"]))
    unallocated = None
    if offers is None and key not in (ps.COST, ps.ADJUSTMENT):
        unallocated = sum((-Decimal(a) for n, a in value["unallocated"]["by_type"].items() if ps.group_of(n) == key),
                          Decimal(0)) or None
    catalog = {o: pid for o, pid in await db.execute(select(Product.offer_id, Product.id).where(Product.user_id == store_id))}
    for p in products:
        p["id"] = str(catalog[p["offer_id"]]) if p["offer_id"] in catalog else None
    return _out({"key": key, "title": ps.TITLES[key], "products": products, "unallocated": unallocated})


async def product_lines(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date, offer_id: str) -> dict:
    """Исходные строки начислений товара за период — как их относит «Прибыль по товарам»; итог = «начислено Ozon»."""
    req = _check(date_from, date_to)
    cov = await pnl_data.coverage(db, store_id, date_from, date_to, req.source)
    lines = await pnl_data.load_lines(db, store_id, cov)
    run = await product_pnl.runner(db, store_id, req, lines)
    mine = []
    for ln in lines:
        offer, via_posting, _ = product_engine.attribute(ln.offer_id, ln.posting_number, run.posting_offers)
        if offer == offer_id:
            mine.append((ln, via_posting))
    # по дням, внутри — по отправлению: сначала выручка, потом удержания по нему
    mine.sort(key=lambda x: (x[0].day, x[0].posting_number or "", ps.group_of(x[0].type_name) != ps.REVENUE,
                             x[0].type_name, x[0].amount))
    reports = {r.id: r for r in cov.reports}
    missing = {rid for ln, _ in mine if (rid := ln.report_id) and rid not in reports}
    if missing:
        reports |= {r.id: r for r in (await db.execute(select(Report).where(Report.id.in_(missing)))).scalars()}
    total = sum((ln.amount for ln, _ in mine), Decimal(0))
    out_lines = [{
        "day": ln.day, "type_name": ln.type_name, "group": ps.group_of(ln.type_name), "amount": ln.amount,
        "quantity": ln.quantity, "posting_number": ln.posting_number, "via_posting": via,
        "source": ln.source, "report": {"id": str(ln.report_id), "filename": reports[ln.report_id].filename,
                                        "period": reports[ln.report_id].period}
        if ln.report_id and ln.report_id in reports else None,
    } for ln, via in mine[:MAX_LINES]]
    return _out({"offer_id": offer_id, "date_from": date_from, "date_to": date_to, "total": total,
                 "count": len(mine), "lines": out_lines})
