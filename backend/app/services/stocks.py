"""Текущие остатки для API: последний снепшот по каждой паре товар × склад + дни запаса и статус (шаг 1.3),
сводка по складам, «Требуют внимания» и «Заканчиваются» (шаг 5.1).

Склада в начислениях Ozon нет, поэтому продажи в день, дни запаса и статус — по товару в целом: весь остаток
на всех складах / продажи в день (шаг 3.5). Пороги статуса — настройки пользователя (5.1). Нет данных — null,
а не 0 (правило 12).
"""
import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.stocks import CRITICAL, EXCESS, LOW, NORMAL, Thresholds, days_of_supply, days_without_sales, stock_status
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse
from app.schemas.sync import utc
from app.services.sales import avg_daily_by_product, data_range, last_sales
from app.services.sync.finance import today_msk
from app.services.user_settings import load_thresholds

SALES_WINDOW_DAYS = 30
NO_DATA = "no_data"  # значение фильтра status для строк без данных о продажах
STATUS_KEYS = (CRITICAL, LOW, NORMAL, EXCESS, NO_DATA)
TOP_PER_WAREHOUSE = 5
ATTENTION_LIMIT = 10
STATUS_TEXT = {CRITICAL: "критично", LOW: "мало", NORMAL: "норма", EXCESS: "избыток"}


@dataclass
class ProductInfo:
    """Товар со всеми его остатками: от него — дни запаса, статус и «сколько не продавался»."""

    product: Product
    places: list[tuple[ProductStock, Warehouse]] = field(default_factory=list)  # последние снепшоты, и нулевые
    available: int = 0  # доступно к продаже на всех складах
    in_transit: int | None = None  # в поставках в пути (FBO); ни одного значения — None
    avg_daily_sales: Decimal | None = None  # null — мало данных о продажах
    days_of_supply: Decimal | None = None
    status: str | None = None  # critical | low | normal | excess | None — нет данных
    last_sale: date | None = None
    days_without_sales: int | None = None  # None — продаж в данных нет

    @property
    def warehouses_in_stock(self) -> int:
        return sum(1 for s, _ in self.places if s.present > 0)


@dataclass
class StocksView:
    """Всё об остатках пользователя — загружается один раз на запрос."""

    thresholds: Thresholds
    today: date
    sales_known: bool  # продажи в день посчитаны (данных о продажах за 30 дней хватает)
    sales_since: date | None  # первый день, за который есть данные о продажах
    products: dict[uuid.UUID, ProductInfo]
    synced_at: datetime | None  # последняя синхронизация складов


@dataclass
class StockRow:
    stock: ProductStock
    product: Product
    warehouse: Warehouse
    avg_daily_sales: Decimal | None  # по товару в целом
    days_of_supply: Decimal | None  # остаток товара на всех складах / продажи в день
    status: str | None
    product_available: int  # доступно к продаже на всех складах
    last_sale: date | None = None
    days_without_sales: int | None = None


async def avg_daily_sales(db: AsyncSession, user_id: uuid.UUID, days: int = SALES_WINDOW_DAYS) -> dict[uuid.UUID, Decimal]:
    """Продажи в день по товару за `days` дней (шаг 3.5, `services/sales`). Склада в начислениях Ozon нет —
    продажи и дни запаса по товару в целом: весь остаток на всех складах / продажи в день.
    Мало дней с данными — пусто, и дни запаса со статусом None («нет данных»), а не 0."""
    return await avg_daily_by_product(db, user_id, days)


async def latest_stocks(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> list[tuple[ProductStock, Product, Warehouse]]:
    latest = (
        select(ProductStock.product_id, ProductStock.warehouse_id, func.max(ProductStock.snapshot_at).label("mx"))
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(Warehouse.user_id == user_id)
        .group_by(ProductStock.product_id, ProductStock.warehouse_id)
        .subquery()
    )
    q = (
        select(ProductStock, Product, Warehouse)
        .join(
            latest,
            and_(
                ProductStock.product_id == latest.c.product_id,
                ProductStock.warehouse_id == latest.c.warehouse_id,
                ProductStock.snapshot_at == latest.c.mx,
            ),
        )
        .join(Product, Product.id == ProductStock.product_id)
        .join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(Product.user_id == user_id)
        .order_by(Product.offer_id, Warehouse.name)
    )
    if warehouse_id:
        q = q.where(ProductStock.warehouse_id == warehouse_id)
    if product_id:
        q = q.where(ProductStock.product_id == product_id)
    return [tuple(r) for r in (await db.execute(q)).all()]


async def load_view(db: AsyncSession, user_id: uuid.UUID, *, product_id: uuid.UUID | None = None) -> StocksView:
    thresholds = await load_thresholds(db, user_id)
    sales = await avg_daily_sales(db, user_id)  # собирает sales_daily при первом чтении — раньше, чем last_sales
    today = today_msk()
    last = await last_sales(db, user_id)
    span = await data_range(db, user_id)
    products: dict[uuid.UUID, ProductInfo] = {}
    for stock, product, warehouse in await latest_stocks(db, user_id, product_id=product_id):
        info = products.setdefault(product.id, ProductInfo(product))
        info.places.append((stock, warehouse))
        info.available += max(stock.present or 0, 0)
        if stock.in_transit is not None:
            info.in_transit = (info.in_transit or 0) + stock.in_transit
    for info in products.values():
        avg = sales.get(info.product.id)
        info.avg_daily_sales = avg
        info.days_of_supply = days_of_supply(info.available, avg)
        info.status = stock_status(info.available, avg, thresholds)
        info.last_sale = last.get(info.product.offer_id)
        info.days_without_sales = days_without_sales(info.last_sale, today)
    synced = await db.scalar(select(func.max(Warehouse.synced_at)).where(Warehouse.user_id == user_id))
    return StocksView(
        thresholds=thresholds, today=today, sales_known=bool(sales), sales_since=span[0] if span else None,
        products=products, synced_at=utc(synced) if synced else None,
    )


def _is_empty(stock: ProductStock) -> bool:
    return not stock.present and not stock.reserved and not stock.in_transit


def view_rows(
    view: StocksView, *, warehouse_id: uuid.UUID | None = None, status: str | None = None, include_empty: bool = False
) -> list[StockRow]:
    rows: list[StockRow] = []
    for info in view.products.values():
        if status and (info.status or NO_DATA) != status:
            continue
        for stock, warehouse in info.places:
            if warehouse_id and warehouse.id != warehouse_id:
                continue
            if not include_empty and _is_empty(stock):
                continue
            rows.append(StockRow(
                stock, info.product, warehouse, info.avg_daily_sales, info.days_of_supply, info.status,
                info.available, info.last_sale, info.days_without_sales,
            ))
    rows.sort(key=lambda r: (r.product.offer_id, r.warehouse.name))
    return rows


async def stock_rows(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: str | None = None,
    include_empty: bool = False,
) -> list[StockRow]:
    view = await load_view(db, user_id, product_id=product_id)
    return view_rows(view, warehouse_id=warehouse_id, status=status, include_empty=include_empty)


# ── сводка по складам (шаг 5.1) ──
def product_out(info: ProductInfo) -> dict:
    p = info.product
    return {
        "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku},
        "available": info.available, "in_transit": info.in_transit, "avg_daily_sales": info.avg_daily_sales,
        "days_of_supply": info.days_of_supply, "status": info.status, "last_sale": info.last_sale,
        "days_without_sales": info.days_without_sales, "warehouses": info.warehouses_in_stock,
    }


def _counts(statuses: list[str | None]) -> dict[str, int]:
    out = dict.fromkeys(STATUS_KEYS, 0)
    for s in statuses:
        out[s or NO_DATA] += 1
    return out


def running_out(view: StocksView) -> list[ProductInfo]:
    """Заканчиваются: «Критично» и «Мало» — меньше всего дней запаса первыми. Архив Ozon не показываем:
    его продавец убрал сам."""
    items = [i for i in view.products.values() if i.status in (CRITICAL, LOW) and not i.product.is_archived]
    return sorted(items, key=lambda i: (i.days_of_supply or Decimal(0), i.product.offer_id))


def lying(view: StocksView) -> list[ProductInfo]:
    """Лежат без движения: «Избыток» — дольше не продававшиеся первыми (продаж в данных нет — самые первые),
    при равных — с большим остатком."""
    items = [i for i in view.products.values() if i.status == EXCESS and i.available > 0]
    never = 10 ** 6
    return sorted(items, key=lambda i: (
        -(never if i.days_without_sales is None else i.days_without_sales), -i.available, i.product.offer_id,
    ))


async def summary(db: AsyncSession, user_id: uuid.UUID) -> dict:
    view = await load_view(db, user_id)
    warehouses = list((await db.execute(
        select(Warehouse).where(Warehouse.user_id == user_id)
        .order_by(Warehouse.is_active.desc(), Warehouse.type, Warehouse.cluster_name, Warehouse.name)
    )).scalars())
    by_warehouse: dict[uuid.UUID, list[tuple[ProductStock, ProductInfo]]] = {w.id: [] for w in warehouses}
    for info in view.products.values():
        for stock, warehouse in info.places:
            if not _is_empty(stock):
                by_warehouse[warehouse.id].append((stock, info))
    out = []
    for w in warehouses:
        here = by_warehouse[w.id]
        top = sorted(here, key=lambda x: (-x[0].present, x[1].product.offer_id))[:TOP_PER_WAREHOUSE]
        out.append({
            "id": w.id, "ozon_warehouse_id": w.ozon_warehouse_id, "name": w.name, "type": w.type,
            "cluster_id": w.cluster_id, "cluster_name": w.cluster_name, "is_active": w.is_active,
            "synced_at": utc(w.synced_at),
            "skus": sum(1 for s, _ in here if s.present > 0),
            "total_present": sum(s.present for s, _ in here),
            "total_in_transit": sum(s.in_transit or 0 for s, _ in here),
            "counts": _counts([i.status for _, i in here]),
            "top": [{**product_out(i), "present": s.present, "in_transit_here": s.in_transit} for s, i in top],
        })
    stocked = [i for i in view.products.values() if any(not _is_empty(s) for s, _ in i.places)]
    ending, idle = running_out(view), lying(view)
    from app.services import stock_table  # себестоимость с НДС — там же, где стоимость запаса (10.17)

    missing = stock_table.no_cost(view, await stock_table.unit_costs(db, user_id, view.today))
    return {
        "thresholds": {"critical": view.thresholds.critical, "low": view.thresholds.low, "excess": view.thresholds.excess},
        "sales_known": view.sales_known, "stocks_known": bool(warehouses), "synced_at": view.synced_at,
        "totals": {
            "products": len(stocked),
            "present": sum(i.available for i in stocked),
            "in_transit": sum(i.in_transit or 0 for i in stocked),
            "counts": _counts([i.status for i in stocked]),
        },
        "warehouses": out,
        "running_out": [product_out(i) for i in ending[:ATTENTION_LIMIT]],
        "running_out_total": len(ending),
        "lying": [product_out(i) for i in idle[:ATTENTION_LIMIT]],
        "lying_total": len(idle),
        "no_cost": [product_out(i) for i in missing[:ATTENTION_LIMIT]],
        "no_cost_total": len(missing),
    }


async def running_out_widget(db: AsyncSession, user_id: uuid.UUID, limit: int) -> dict:
    """Виджет «Заканчиваются» на главной (шаг 5.1)."""
    view = await load_view(db, user_id)
    ending = running_out(view)
    return {
        "thresholds": {"critical": view.thresholds.critical, "low": view.thresholds.low, "excess": view.thresholds.excess},
        "sales_known": view.sales_known, "stocks_known": view.synced_at is not None,
        "critical": sum(1 for i in ending if i.status == CRITICAL),
        "low": sum(1 for i in ending if i.status == LOW),
        "items": [product_out(i) for i in ending[:limit]],
    }


# ── CSV ──
CSV_COLUMNS = (
    "Артикул", "Название", "SKU", "Склад", "Тип склада", "Кластер", "Доступно, шт.", "Резерв, шт.", "В пути, шт.",
    "Продажи в день, шт.", "Дней запаса", "Доступно на всех складах, шт.", "Статус", "Последняя продажа",
    "Дней без продаж",
)


def _csv(v) -> str:
    if v is None:
        return ""
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, Decimal):
        return str(v).replace(".", ",")
    return str(v)


def to_csv(rows: list[StockRow]) -> bytes:
    """CSV для Excel: UTF-8 с BOM, «;», дробные — с запятой, даты ДД.ММ.ГГГГ (как у товаров)."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        p, wh, s = r.product, r.warehouse, r.stock
        w.writerow([_csv(v) for v in (
            p.offer_id, p.name, p.ozon_sku, wh.name, wh.type, wh.cluster_name, s.present, s.reserved, s.in_transit,
            r.avg_daily_sales, r.days_of_supply, r.product_available,
            STATUS_TEXT[r.status] if r.status else ("нет данных" if r.avg_daily_sales is None else ""),
            r.last_sale, r.days_without_sales,
        )])
    return ("﻿" + out.getvalue()).encode("utf-8")
