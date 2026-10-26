"""«Остатки» — главная таблица (шаг 10.17, SITEMAP п. 4.1): по товарам, складам или кластерам — доступно, в резерве, в
пути, продажи в день, дни запаса, дата окончания, стоимость запаса по себестоимости, статус, последняя продажа.

Данные — `stocks.load_view` (последние снимки по складам, продажи в день и статус по товару в целом, пороги 5.1). Дни
запаса, дата окончания и статус — по товару в целом (склада в начислениях Ozon нет); количество и стоимость — по строке
(товар, склад или кластер). Себестоимость — справочник на сегодня с НДС поставщика (как «Все товары»); нет её —
стоимость «нет данных», а не 0 (правило 5). Фильтры: статус остатка (5.1) и «нет продаж» — продажи за 30 дней известны и
равны нулю при остатке.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.stocks import end_date, stock_value
from app.models.warehouse import FBO, ProductStock, Warehouse
from app.services.costs import load_cost_history
from app.services.stocks import NO_DATA, STATUS_TEXT, ProductInfo, StocksView, load_view
from app.services.tax_profile import supplier_vat_multiplier
from app.services.xlsx_export import Col, table_xlsx

PRODUCT, WAREHOUSE, CLUSTER = "product", "warehouse", "cluster"
GROUPS = (PRODUCT, WAREHOUSE, CLUSTER)
NO_SALES = "no_sales"
SORTS = ("offer_id", "available", "reserved", "in_transit", "avg_daily_sales", "days_of_supply", "end_date",
         "stock_value", "last_sale")


@dataclass
class Row:
    info: ProductInfo
    places: list[tuple[ProductStock, Warehouse]] = field(default_factory=list)
    warehouse: Warehouse | None = None  # группировка по складам
    cluster: str | None = None  # группировка по кластерам: кластер Ozon или свой склад
    cluster_type: str | None = None


def _sum(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _cluster_of(w: Warehouse) -> tuple[str, str]:
    """Кластер Ozon (FBO) или свой склад (FBS / rFBS) — сам себе «кластер»."""
    if w.type == FBO and w.cluster_name:
        return w.cluster_name, FBO
    return w.name, w.type


def _rows(view: StocksView, group: str, warehouse_id: uuid.UUID | None, include_empty: bool = False) -> list[Row]:
    out: list[Row] = []
    for info in view.products.values():
        places = [(s, w) for s, w in info.places if (warehouse_id is None or w.id == warehouse_id)
                  and (include_empty or s.present or s.reserved or s.in_transit)]
        if not places:
            continue
        if group == PRODUCT:
            out.append(Row(info, places))
        elif group == WAREHOUSE:
            out.extend(Row(info, [(s, w)], warehouse=w) for s, w in places)
        else:
            by: dict[tuple[str, str], list] = {}
            for s, w in places:
                by.setdefault(_cluster_of(w), []).append((s, w))
            out.extend(Row(info, ps, cluster=name, cluster_type=kind) for (name, kind), ps in by.items())
    return out


def _passes(row: Row, status: str | None) -> bool:
    if status is None:
        return True
    info = row.info
    if status == NO_SALES:
        return info.avg_daily_sales is not None and info.avg_daily_sales <= 0 and info.available > 0
    return (info.status or NO_DATA) == status


def _matches(row: Row, search: str | None) -> bool:
    if not search:
        return True
    p = row.info.product
    q = search.casefold()
    return any(q in (v or "").casefold() for v in (p.offer_id, p.name, str(p.ozon_sku or "")))


async def unit_costs(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict[str, Decimal]:
    """Себестоимость с НДС поставщика на сегодня по артикулу; нет записи — артикула нет в словаре."""
    history = await load_cost_history(db, user_id)
    default_vat = await supplier_vat_multiplier(db, user_id, today)
    out = {}
    for offer in history.offers():
        rec = history.record_at(offer, today)
        if rec is not None:
            out[offer] = (Decimal(rec.cost_price) * Decimal(rec.vat_multiplier or default_vat)).quantize(Decimal("0.01"))
    return out


def out_row(row: Row, today: date, costs: dict[str, Decimal]) -> dict:
    info, p = row.info, row.info.product
    available = sum(max(s.present or 0, 0) for s, _ in row.places)
    unit_cost = costs.get(p.offer_id)
    w = row.warehouse
    return {
        "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku},
        "warehouse": {"id": w.id, "name": w.name, "type": w.type, "cluster_name": w.cluster_name} if w else None,
        "cluster": row.cluster, "cluster_type": row.cluster_type,
        "places": len({wh.id for s, wh in row.places if s.present}),
        "available": available,
        "reserved": _sum([s.reserved for s, _ in row.places]),
        "in_transit": _sum([s.in_transit for s, _ in row.places]),
        "product_available": info.available,
        "avg_daily_sales": info.avg_daily_sales, "days_of_supply": info.days_of_supply,
        "end_date": end_date(info.available, info.avg_daily_sales, today),
        "sells": None if info.avg_daily_sales is None else info.avg_daily_sales > 0,
        "unit_cost": unit_cost, "stock_value": stock_value(available, unit_cost),
        "status": info.status, "last_sale": info.last_sale, "days_without_sales": info.days_without_sales,
    }


def _sort(rows: list[dict], sort: str, order: str) -> list[dict]:
    """Пустые значения — в конце при любом порядке, при равных — по артикулу и складу."""
    def name(r: dict) -> tuple:
        return (r["product"]["offer_id"].casefold(), (r["warehouse"] or {}).get("name") or r["cluster"] or "")

    base = sorted(rows, key=name)
    if sort == "offer_id":
        return base if order == "asc" else sorted(rows, key=name, reverse=True)
    filled = [r for r in base if r[sort] is not None]
    empty = [r for r in base if r[sort] is None]
    return sorted(filled, key=lambda r: r[sort], reverse=order == "desc") + empty


async def table(
    db: AsyncSession, user_id: uuid.UUID, *, group: str = PRODUCT, warehouse_id: uuid.UUID | None = None,
    status: str | None = None, search: str | None = None, sort: str = "offer_id", order: str = "asc",
    include_empty: bool = False,
) -> dict:
    view = await load_view(db, user_id)
    costs = await unit_costs(db, user_id, view.today)
    rows = [r for r in _rows(view, group, warehouse_id, include_empty) if _passes(r, status) and _matches(r, search)]
    out = _sort([out_row(r, view.today, costs) for r in rows], sort, order)
    valued = [r for r in out if r["stock_value"] is not None]
    t = view.thresholds
    return {
        "group": group, "today": view.today, "sales_known": view.sales_known, "stocks_known": view.synced_at is not None,
        "thresholds": {"critical": t.critical, "low": t.low, "excess": t.excess},
        "totals": {
            "rows": len(out), "products": len({r["product"]["id"] for r in out}),
            "available": sum(r["available"] for r in out),
            "in_transit": sum(r["in_transit"] or 0 for r in out),
            "stock_value": sum((r["stock_value"] for r in valued), Decimal(0)) if valued else None,
            "without_cost": len({r["product"]["id"] for r in out if r["stock_value"] is None and r["available"] > 0}),
        },
        "rows": out,
    }


def no_cost(view: StocksView, costs: dict[str, Decimal]) -> list[ProductInfo]:
    """Лежат на складах без себестоимости — стоимость запаса не посчитать: больше штук — первыми."""
    items = [i for i in view.products.values()
             if i.available > 0 and i.product.offer_id not in costs and not i.product.is_archived]
    return sorted(items, key=lambda i: (-i.available, i.product.offer_id))


# ── Excel ──
COLUMNS = [
    Col("Артикул", 24), Col("Название", 36), Col("Склад или кластер", 26), Col("Доступно, шт.", 11, "int"),
    Col("Резерв, шт.", 10, "int"), Col("В пути, шт.", 10, "int"), Col("Продажи в день", 11, "number"),
    Col("Дней запаса", 10, "number"), Col("Закончится", 12, "date"), Col("Себестоимость с НДС, ₽", 13, "money"),
    Col("Стоимость запаса, ₽", 14, "money"), Col("Статус", 12), Col("Последняя продажа", 13, "date"),
]
GROUP_TITLE = {PRODUCT: "по товарам", WAREHOUSE: "по складам", CLUSTER: "по кластерам"}


def to_xlsx(data: dict) -> bytes:
    rows = []
    for r in data["rows"]:
        where = r["warehouse"]["name"] if r["warehouse"] else (r["cluster"] or f"складов: {r['places']}")
        status = STATUS_TEXT.get(r["status"], "нет данных") if r["status"] or r["avg_daily_sales"] is None else ""
        end = r["end_date"] if r["end_date"] else ("не продаётся" if r["sells"] is False else None)
        rows.append([
            r["product"]["offer_id"], r["product"]["name"], where, r["available"], r["reserved"], r["in_transit"],
            r["avg_daily_sales"], r["days_of_supply"], end, r["unit_cost"],
            r["stock_value"] if r["stock_value"] is not None else "нет себестоимости", status, r["last_sale"],
        ])
    t = data["totals"]
    notes = [
        f"Остатки {GROUP_TITLE[data['group']]} — {data['today']:%d.%m.%Y}",
        "",
        "Продажи в день — по товару в целом (по всем складам), за 30 дней с данными; дни запаса и дата окончания — весь "
        "остаток товара / продажи в день.",
        "Стоимость запаса — остаток × себестоимость с НДС на сегодня; нет себестоимости — не посчитана (а не 0).",
        f"Итого: {t['available']} шт., стоимость {t['stock_value'] or 0:.2f} ₽"
        + (f"; без себестоимости — {t['without_cost']} товаров" if t["without_cost"] else ""),
    ]
    total = ["Итого", None, None, t["available"], None, t["in_transit"], None, None, None, None, t["stock_value"], None, None]
    return table_xlsx("Остатки", COLUMNS, rows, total=total, notes_title="Пояснения", notes=notes)
