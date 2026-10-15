"""Страница товаров (шаг 4.1): товар каталога Ozon со всем, что о нём уже известно сервису.

Своих таблиц нет — строка собирается из готового: цена и комиссия — последний снимок `product_prices` (1.2),
остаток — последние снимки по складам (1.3), продажи в день и дни запаса — `sales_daily` (3.5), себестоимость —
справочник на сегодня (2.3), акции — текущее участие (1.5). Чего нет — null, а не 0 (правила 5 и 12).
"""
import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import products as engine
from app.engine.promotions import margin_level
from app.engine.stocks import days_of_supply, stock_status
from app.models.product import Product, ProductPrice
from app.models.promotion import Promotion, PromotionProduct
from app.models.sales_daily import SalesDaily
from app.models.warehouse import ProductStock, Warehouse
from app.schemas.products import ProductPromo
from app.schemas.sync import utc
from app.services.costs import CostHistory, load_cost_history
from app.services.stocks import avg_daily_sales, latest_stocks
from app.services.sync.finance import MSK, today_msk
from app.services.sync.products import latest_prices
from app.services.tax_profile import supplier_vat_multiplier

MARGIN_NOTE = (
    "Маржа при текущей цене: цена − комиссия Ozon − эквайринг − себестоимость с НДС поставщика, в % от цены. "
    "Логистика, хранение и реклама сюда не входят — сколько Ozon фактически удержал по товару, видно "
    "в «Прибыли по товарам»."
)

STATUS_TEXT = {"critical": "критично", "low": "мало", "normal": "норма", "excess": "избыток"}
INDEX_TEXT = {"super": "супер", "green": "выгодный", "yellow": "умеренный", "red": "невыгодный"}

_CENT = Decimal("0.01")


def money(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(_CENT)  # одинаково для PostgreSQL и SQLite


@dataclass
class Context:
    """Всё, из чего собираются строки товаров, — загружается один раз на запрос."""

    today: date
    prices: dict[uuid.UUID, ProductPrice]
    stocks: dict[uuid.UUID, tuple[int, int | None]]  # доступно на всех складах, в пути
    stocks_known: bool
    sales: dict[uuid.UUID, Decimal]  # продажи в день; пусто — мало данных
    history: CostHistory
    default_vat: Decimal
    promos: dict[uuid.UUID, list[ProductPromo]] = field(default_factory=dict)
    last_sale: dict[str, date] = field(default_factory=dict)


async def _promotions(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID | None) -> dict[uuid.UUID, list[ProductPromo]]:
    """Текущее участие: товар среди участников акции, которая ещё не закончилась."""
    q = (
        select(PromotionProduct, Promotion)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .where(
            Promotion.user_id == user_id, PromotionProduct.is_active.is_(True),
            or_(Promotion.date_to.is_(None), Promotion.date_to >= datetime.now(timezone.utc)),
        )
        .order_by(Promotion.date_to, Promotion.title)
    )
    if product_id:
        q = q.where(PromotionProduct.product_id == product_id)
    out: dict[uuid.UUID, list[ProductPromo]] = {}
    for r, promo in await db.execute(q):
        out.setdefault(r.product_id, []).append(ProductPromo(
            id=promo.id, title=promo.title, action_price=r.action_price, margin_in_promo=r.margin_in_promo,
            added_automatically=r.added_automatically, date_to=utc(promo.date_to),
        ))
    return out


async def _last_sales(db: AsyncSession, user_id: uuid.UUID, offer_id: str | None) -> dict[str, date]:
    q = (
        select(SalesDaily.offer_id, func.max(SalesDaily.day))
        .where(SalesDaily.user_id == user_id, SalesDaily.qty_sold > 0)
        .group_by(SalesDaily.offer_id)
    )
    if offer_id is not None:
        q = q.where(SalesDaily.offer_id == offer_id)
    return {offer: day for offer, day in await db.execute(q)}


async def _has_stocks(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Остатки хоть раз загружались: тогда товара без снимков нет ни на одном складе (0), иначе — нет данных."""
    found = await db.scalar(
        select(ProductStock.id).join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
        .where(Warehouse.user_id == user_id).limit(1)
    )
    return found is not None


async def load_context(db: AsyncSession, user_id: uuid.UUID, products: list[Product], one: Product | None = None) -> Context:
    stocks: dict[uuid.UUID, tuple[int, int | None]] = {}
    snapshots = await latest_stocks(db, user_id, product_id=one.id if one else None)
    for stock, product, _ in snapshots:
        present, transit = stocks.get(product.id, (0, None))
        if stock.in_transit is not None:
            transit = (transit or 0) + stock.in_transit
        stocks[product.id] = (present + max(stock.present or 0, 0), transit)
    sales = await avg_daily_sales(db, user_id)  # собирает sales_daily при первом чтении — раньше, чем last_sale
    return Context(
        today=today_msk(),
        prices=await latest_prices(db, [p.id for p in products]),
        stocks=stocks,
        stocks_known=bool(snapshots) or await _has_stocks(db, user_id),
        sales=sales,
        history=await load_cost_history(db, user_id),
        default_vat=await supplier_vat_multiplier(db, user_id),
        promos=await _promotions(db, user_id, one.id if one else None),
        last_sale=await _last_sales(db, user_id, one.offer_id if one else None),
    )


def build_row(ctx: Context, p: Product) -> dict:
    snap = ctx.prices.get(p.id)
    price = engine.positive(snap.price) if snap else None
    commission_pct = snap.commission_pct if snap else None
    acquiring_pct = snap.acquiring_pct if snap else None
    record = ctx.history.record_at(p.offer_id, ctx.today)
    cost = money(record.cost_price) if record else None
    cost_with_vat = money(record.cost_price * (record.vat_multiplier or ctx.default_vat)) if record else None
    stock, transit = ctx.stocks.get(p.id, (None, None))
    if stock is None and ctx.stocks_known:
        stock = 0  # остатки загружались, а этого товара ни на одном складе нет
    avg = ctx.sales.get(p.id)
    margin = engine.current_margin(price, cost_with_vat, commission_pct, acquiring_pct)
    return {
        "id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku, "ozon_product_id": p.ozon_product_id,
        "is_archived": p.is_archived,
        "price": money(price), "old_price": money(engine.positive(snap.old_price)) if snap else None,
        "marketing_price": money(engine.positive(snap.marketing_price)) if snap else None,
        "min_price": money(engine.positive(snap.min_price)) if snap else None,
        "commission_pct": commission_pct, "commission": engine.percent_of(price, commission_pct),
        "acquiring_pct": acquiring_pct, "price_updated_at": utc(snap.snapshot_at) if snap else None,
        "cost_price": cost, "cost_with_vat": cost_with_vat,
        "stock": stock, "in_transit": transit, "avg_daily_sales": avg,
        "days_of_supply": days_of_supply(stock, avg) if stock is not None else None,
        "stock_status": stock_status(stock, avg) if stock is not None else None,
        "unit_profit": engine.unit_profit(price, cost_with_vat, commission_pct, acquiring_pct),
        "margin": margin, "margin_level": margin_level(margin),
        "price_index": engine.price_index_level(snap.price_index) if snap else None,
        "promotions": ctx.promos.get(p.id, []),
        "last_sale": ctx.last_sale.get(p.offer_id),
    }


async def product_rows(db: AsyncSession, user_id: uuid.UUID) -> tuple[list[dict], Context]:
    products = list((await db.execute(select(Product).where(Product.user_id == user_id))).scalars())
    ctx = await load_context(db, user_id, products)
    return [build_row(ctx, p) for p in products], ctx


def count(rows: list[dict]) -> dict:
    def n(f: str) -> int:
        return sum(engine.passes(r, {f}) for r in rows)

    return {"all": len(rows), "no_cost": n(engine.NO_COST), "in_promo": n(engine.IN_PROMO),
            "critical_stock": n(engine.CRITICAL_STOCK), "low_stock": n(engine.LOW_STOCK), "negative": n(engine.NEGATIVE)}


def select_rows(
    rows: list[dict], *, search: str | None, filters: set[str], archived: bool, sort: str, order: str
) -> tuple[list[dict], dict]:
    """Строки страницы (архивные или в продаже → поиск → быстрые фильтры → сортировка) и счётчики.
    Счётчики — по всем товарам вкладки, без поиска и фильтров: сколько всего проблем."""
    base = [r for r in rows if r["is_archived"] == archived]
    counts = {**count(base), "archived": sum(r["is_archived"] for r in rows)}
    found = [
        r for r in base
        if (not search or engine.matches(search, r["offer_id"], r["name"], r["ozon_sku"], r["ozon_product_id"]))
        and engine.passes(r, filters)
    ]
    return engine.sort_rows(found, sort, order), counts


# ── CSV ──
CSV_COLUMNS = [
    ("offer_id", "Артикул"), ("name", "Название"), ("ozon_sku", "SKU"), ("price", "Цена, руб."),
    ("min_price", "Мин. цена в Ozon, руб."), ("commission_pct", "Комиссия, %"), ("commission", "Комиссия, руб."),
    ("cost_price", "Себестоимость без НДС, руб."), ("cost_with_vat", "Себестоимость с НДС, руб."),
    ("stock", "Остаток, шт."), ("in_transit", "В пути, шт."), ("avg_daily_sales", "Продажи в день, шт."),
    ("days_of_supply", "Дней запаса"), ("stock_status", "Остаток"), ("unit_profit", "Остаётся с 1 шт., руб."),
    ("margin", "Маржа, %"), ("price_index", "Индекс цены"), ("promotions", "Акции"),
    ("last_sale", "Последняя продажа"), ("is_archived", "В архиве"),
]


def _cell(key: str, v) -> str:
    if v is None:
        return ""
    if key == "stock_status":
        return STATUS_TEXT.get(v, v)
    if key == "price_index":
        return INDEX_TEXT.get(v, v)
    if key == "promotions":
        return ", ".join(p.title for p in v)
    if key == "is_archived":
        return "да" if v else ""
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, Decimal):
        return str(v).replace(".", ",")
    return str(v)


def to_csv(rows: list[dict]) -> bytes:
    """CSV для Excel: UTF-8 с BOM, «;», дробные — с запятой, даты ДД.ММ.ГГГГ."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow([title for _, title in CSV_COLUMNS])
    for r in rows:
        w.writerow([_cell(key, r[key]) for key, _ in CSV_COLUMNS])
    return ("﻿" + out.getvalue()).encode("utf-8")


# ── карточка товара ──
def _msk_day(dt: datetime) -> date:
    return utc(dt).astimezone(MSK).date()


async def price_history(db: AsyncSession, product_id: uuid.UUID, date_from: date, date_to: date) -> tuple[list, list, datetime | None]:
    """Цена по дням за период (с учётом снимка до начала периода) и снимки внутри периода, новые — первыми."""
    start = datetime.combine(date_from, time(), MSK).astimezone(timezone.utc)
    inside = list((await db.execute(
        select(ProductPrice).where(ProductPrice.product_id == product_id, ProductPrice.snapshot_at >= start)
        .order_by(ProductPrice.snapshot_at)
    )).scalars())
    before = (await db.execute(
        select(ProductPrice).where(ProductPrice.product_id == product_id, ProductPrice.snapshot_at < start)
        .order_by(ProductPrice.snapshot_at.desc()).limit(1)
    )).scalar_one_or_none()
    first = await db.scalar(select(func.min(ProductPrice.snapshot_at)).where(ProductPrice.product_id == product_id))
    points = [
        engine.PricePoint(_msk_day(s.snapshot_at), s.price, s.min_price, s.marketing_price)
        for s in ([before] if before else []) + inside
    ]
    days = engine.daily_prices(points, date_from, date_to)
    for d in days:
        for key in ("price", "min_price", "marketing_price"):
            d[key] = money(d[key])
    changes = [
        {"at": utc(s.snapshot_at), "price": money(engine.positive(s.price)), "old_price": money(engine.positive(s.old_price)),
         "min_price": money(engine.positive(s.min_price)), "marketing_price": money(engine.positive(s.marketing_price)),
         "commission_pct": s.commission_pct}
        for s in reversed(inside)
    ]
    return days, changes, utc(first)


async def detail(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID, days: int) -> dict | None:
    product = await db.get(Product, product_id)
    if product is None or product.user_id != user_id:
        return None
    ctx = await load_context(db, user_id, [product], one=product)
    row = build_row(ctx, product)
    record = ctx.history.record_at(product.offer_id, ctx.today)
    cost = None
    if record is not None:
        vat = record.vat_multiplier or ctx.default_vat
        cost = {
            "cost_price": money(record.cost_price), "vat_multiplier": record.vat_multiplier, "supplier_vat": vat,
            "cost_with_vat": money(record.cost_price * vat), "effective_from": record.effective_from, "note": record.note,
            "history_count": len(ctx.history.history(product.offer_id)),
        }
    stocks = [
        {"warehouse": w.name, "type": w.type, "cluster_name": w.cluster_name, "present": s.present,
         "in_transit": s.in_transit, "reserved": s.reserved}
        for s, _, w in await latest_stocks(db, user_id, product_id=product.id)
        if s.present or s.in_transit or s.reserved
    ]
    stocks.sort(key=lambda s: (-s["present"], s["cluster_name"] or "", s["warehouse"]))
    date_to = ctx.today
    price_days, changes, first = await price_history(db, product.id, date_to - timedelta(days=days - 1), date_to)
    return {
        "item": row, "barcode": product.barcode, "category_id": product.category_id, "vat": product.vat,
        "cost": cost, "supplier_vat": ctx.default_vat, "stocks": stocks,
        "price_days": price_days, "price_changes": changes, "first_price_at": first,
    }
