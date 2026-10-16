"""Продажи по товарам по дням (шаг 3.5): пересборка `sales_daily` из начислений и чтение для графиков.

Источник дня — как у P&L (`pnl_data.coverage`, auto): отчёт по начислениям XLSX, если он покрывает день,
иначе начисления Ozon API. Пересборка — за затронутые дни: после синхронизации финансов (день) и после
загрузки, замены, скрытия, восстановления и перечитывания отчёта (его период). Коммит — на вызывающем.
Дни без данных строк не имеют и в ответах — null, а не 0 продаж (правило 12).
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import sales as engine
from app.engine.product_pnl import LineIn
from app.models.accrual import AccrualDay
from app.models.product import Product
from app.models.report import Report
from app.models.sales_daily import SalesDaily
from app.services import pnl_data
from app.services.sync.finance import today_msk

WINDOW_DAYS = 30
ZERO = Decimal(0)


@dataclass
class RebuildStats:
    days: int = 0  # дней в диапазоне
    days_with_data: int = 0
    rows: int = 0  # записано строк (день × артикул)
    unattributed: int = 0  # строк «Выручка» / «Возврат выручки» без товара

    def add(self, other: "RebuildStats") -> None:
        self.days += other.days
        self.days_with_data += other.days_with_data
        self.rows += other.rows
        self.unattributed += other.unattributed


async def rebuild(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> RebuildStats:
    """Пересобрать продажи за [date_from, date_to]: старые строки периода заменяются целиком."""
    cov = await pnl_data.coverage(db, user_id, date_from, date_to, "auto")
    lines = await pnl_data.load_lines(db, user_id, cov)
    numbers = {ln.posting_number for ln in lines if not ln.offer_id and ln.posting_number}
    rows, unattributed = engine.aggregate(
        [LineIn(ln.day, ln.type_name, ln.amount, ln.quantity, ln.offer_id, ln.sku, ln.name, ln.posting_number)
         for ln in lines],
        await pnl_data.posting_offers(db, user_id, numbers),
    )
    products = {offer: (pid, sku) for pid, offer, sku in await db.execute(
        select(Product.id, Product.offer_id, Product.ozon_sku).where(Product.user_id == user_id)
    )}
    await db.execute(
        delete(SalesDaily).where(SalesDaily.user_id == user_id, SalesDaily.day >= date_from, SalesDaily.day <= date_to)
    )
    now = datetime.now(timezone.utc)
    for r in rows:
        product_id, product_sku = products.get(r.offer_id, (None, None))
        db.add(SalesDaily(
            user_id=user_id, day=r.day, offer_id=r.offer_id[:100], product_id=product_id,
            sku=int(r.sku) if r.sku.isdigit() else product_sku, qty_sold=r.qty_sold, qty_returned=r.qty_returned,
            revenue=r.revenue, returns_amount=r.returns_amount, source=cov.days[r.day], calculated_at=now,
        ))
    return RebuildStats(len(cov.days), len(cov.days) - cov.count(None), len(rows), unattributed)


async def data_range(db: AsyncSession, user_id: uuid.UUID) -> tuple[date, date] | None:
    """Первый и последний день, за которые есть начисления (API или отчёты по начислениям)."""
    api = (await db.execute(
        select(func.min(AccrualDay.day), func.max(AccrualDay.day)).where(AccrualDay.user_id == user_id)
    )).one()
    xlsx = (await db.execute(
        select(func.min(Report.period_start), func.max(Report.period_end)).where(
            Report.user_id == user_id, Report.deleted_at.is_(None), Report.report_type == "accrual"
        )
    )).one()
    starts = [d for d in (api[0], xlsx[0]) if d]
    ends = [d for d in (api[1], xlsx[1]) if d]
    return (min(starts), max(ends)) if starts else None


async def rebuild_all(db: AsyncSession, user_id: uuid.UUID) -> RebuildStats:
    """Вся история — по месяцам, каждый месяц своей транзакцией (для данных, загруженных до шага 3.5)."""
    total = RebuildStats()
    span = await data_range(db, user_id)
    if span is None:
        return total
    start, end = span
    while start <= end:
        nxt = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
        stats = await rebuild(db, user_id, start, min(end, nxt - timedelta(days=1)))
        await db.commit()
        total.add(stats)
        start = nxt
    return total


async def ensure_built(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Начисления загружены до шага 3.5, а продаж ещё нет — собрать всю историю один раз."""
    has_rows = await db.scalar(select(SalesDaily.id).where(SalesDaily.user_id == user_id).limit(1))
    if has_rows is None and await data_range(db, user_id) is not None:
        await rebuild_all(db, user_id)


async def daily(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, offer_id: str | None = None
) -> dict:
    """Продажи по дням (товара или всех товаров). Дни без данных — null, дни с данными без продаж — 0."""
    await ensure_built(db, user_id)
    cov = await pnl_data.coverage(db, user_id, date_from, date_to, "auto")
    q = select(
        SalesDaily.day, func.sum(SalesDaily.qty_sold), func.sum(SalesDaily.qty_returned),
        func.sum(SalesDaily.revenue), func.sum(SalesDaily.returns_amount),
    ).where(SalesDaily.user_id == user_id, SalesDaily.day >= date_from, SalesDaily.day <= date_to)
    if offer_id is not None:
        q = q.where(SalesDaily.offer_id == offer_id)
    by_day = {d: r for d, *r in (await db.execute(q.group_by(SalesDaily.day))).all()}
    days, sold, returned, revenue = [], 0, 0, ZERO
    for day, source in cov.days.items():
        if source is None:
            days.append({"date": day.isoformat(), "source": None, "qty_sold": None, "qty_returned": None,
                         "revenue": None, "returns_amount": None})
            continue
        s, r, rev, ret = by_day.get(day, (0, 0, ZERO, ZERO))
        sold, returned, revenue = sold + int(s), returned + int(r), revenue + Decimal(rev)
        days.append({"date": day.isoformat(), "source": source, "qty_sold": int(s), "qty_returned": int(r),
                     "revenue": str(Decimal(rev).quantize(Decimal("0.01"))),
                     "returns_amount": str(Decimal(ret).quantize(Decimal("0.01")))})
    with_data = len(cov.days) - cov.count(None)
    avg = engine.avg_daily(sold - returned, with_data, min_days=1)
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "offer_id": offer_id,
        "data_source": cov.data_source, "days": days,
        "totals": {"qty_sold": sold, "qty_returned": returned, "revenue": str(revenue.quantize(Decimal("0.01"))),
                   "days_with_data": with_data, "days_without_data": cov.count(None),
                   "avg_daily": str(avg) if avg is not None else None},
    }


async def avg_daily_by_product(
    db: AsyncSession, user_id: uuid.UUID, days: int = WINDOW_DAYS, today: date | None = None
) -> dict[uuid.UUID, Decimal]:
    """Продажи в день по товарам каталога за последние `days` полных дней (сегодня не входит).

    Продано за вычетом возвратов / дней с данными. Дней с данными меньше engine.MIN_DAYS — пусто
    («нет данных»). Товар без продаж при достаточных данных — 0 (знаем, что не продавался).
    """
    await ensure_built(db, user_id)
    end = (today or today_msk()) - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    cov = await pnl_data.coverage(db, user_id, start, end, "auto")
    with_data = len(cov.days) - cov.count(None)
    if with_data < engine.MIN_DAYS:
        return {}
    net = {offer: int(s or 0) - int(r or 0) for offer, s, r in await db.execute(
        select(SalesDaily.offer_id, func.sum(SalesDaily.qty_sold), func.sum(SalesDaily.qty_returned))
        .where(SalesDaily.user_id == user_id, SalesDaily.day >= start, SalesDaily.day <= end)
        .group_by(SalesDaily.offer_id)
    )}
    out: dict[uuid.UUID, Decimal] = {}
    for pid, offer in await db.execute(select(Product.id, Product.offer_id).where(Product.user_id == user_id)):
        out[pid] = engine.avg_daily(net.get(offer, 0), with_data)
    return out


async def last_sales(db: AsyncSession, user_id: uuid.UUID, offer_id: str | None = None) -> dict[str, date]:
    """Последний день с продажей по артикулу (страница товаров 4.1, склады 5.1). Нет в ответе — продаж в данных нет."""
    q = (
        select(SalesDaily.offer_id, func.max(SalesDaily.day))
        .where(SalesDaily.user_id == user_id, SalesDaily.qty_sold > 0)
        .group_by(SalesDaily.offer_id)
    )
    if offer_id is not None:
        q = q.where(SalesDaily.offer_id == offer_id)
    return {offer: day for offer, day in await db.execute(q)}
