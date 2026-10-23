"""Себестоимость товаров: справочник в БД с историей цен (шаг 2.3).

- CostHistory — все записи пользователя; `at(день)` даёт CostProvider на дату (для отчёта —
  дата начала его периода), `cost_of` — на сегодня. Парсеры видят только CostProvider.
- Неизвестная себестоимость — None, а не 0 (правило 5).
"""
import bisect
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.anomaly_detector import CostGap
from app.integrations.xlsx.costs import MappingCostProvider
from app.models.product import Product
from app.models.product_cost import BEGINNING, ProductCost
from app.models.report import Report
from app.models.sales_daily import SalesDaily
from app.models.user import User
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk


class CostHistory:
    """История себестоимости пользователя по артикулам."""

    def __init__(self, records: list[ProductCost], today: date | None = None):
        self._today = today or today_msk()
        by_offer: dict[str, list[ProductCost]] = defaultdict(list)
        for r in records:
            by_offer[r.offer_id].append(r)
        self._records = {
            offer: sorted(rs, key=lambda r: (r.effective_from, _created(r))) for offer, rs in by_offer.items()
        }

    def record_at(self, offer_id: str, day: date) -> ProductCost | None:
        """Запись, действующая на дату: последняя с effective_from ≤ day, иначе самая первая."""
        records = self._records.get(offer_id)
        if not records:
            return None
        i = bisect.bisect_right([r.effective_from for r in records], day)
        return records[i - 1] if i else records[0]

    def at(self, day: date) -> MappingCostProvider:
        return MappingCostProvider(
            {offer: _money(rec.cost_price) for offer in self._records if (rec := self.record_at(offer, day))}
        )

    def cost_of(self, article: str) -> Decimal | None:
        rec = self.record_at(article, self._today)
        return _money(rec.cost_price) if rec else None

    def history(self, offer_id: str) -> list[ProductCost]:
        return list(self._records.get(offer_id, []))

    def offers(self) -> list[str]:
        return list(self._records)

    def dates(self, offer_id: str) -> list[date]:
        return [r.effective_from for r in self._records.get(offer_id, [])]


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))  # одинаково для PostgreSQL и SQLite


def _created(r: ProductCost) -> datetime:
    created = r.created_at or datetime.min
    return created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created


async def load_cost_history(db: AsyncSession, user_id: uuid.UUID) -> CostHistory:
    res = await db.execute(select(ProductCost).where(ProductCost.user_id == user_id))
    return CostHistory(list(res.scalars()))


async def sales_without_cost(
    db: AsyncSession, user_id: uuid.UUID, start: date, end: date, covered: set[date], history: CostHistory | None = None
) -> list[CostGap]:
    """Продажи в дни без себестоимости (справочник на день продажи — как P&L) по артикулам: штуки и выручка. Считаются
    только дни с данными о продажах (`covered`): за остальные продажи неизвестны. «Требует внимания» (10.5) и
    «Рекомендуемые действия» (10.6)."""
    history = history or await load_cost_history(db, user_id)
    gaps: dict[str, list] = {}
    for day, offer, qty, revenue in await db.execute(
        select(SalesDaily.day, SalesDaily.offer_id, SalesDaily.qty_sold, SalesDaily.revenue)
        .where(SalesDaily.user_id == user_id, SalesDaily.day >= start, SalesDaily.day <= end, SalesDaily.qty_sold > 0)
    ):
        if day in covered and history.record_at(offer, day) is None:
            g = gaps.setdefault(offer, [0, Decimal(0)])
            g[0] += qty
            g[1] += Decimal(revenue)
    if not gaps:
        return []
    names = {offer: name for offer, name in await db.execute(
        select(Product.offer_id, Product.name).where(Product.user_id == user_id, Product.offer_id.in_(list(gaps))))}
    return [CostGap(offer, names.get(offer), revenue, qty) for offer, (qty, revenue) in gaps.items()]


async def get_cost_provider(
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
) -> CostHistory:
    """Зависимость FastAPI: себестоимость пользователя (с историей) для разбора отчётов."""
    return await load_cost_history(db, user.id)


async def add_cost(
    db: AsyncSession,
    user_id: uuid.UUID,
    offer_id: str,
    cost_price: Decimal,
    vat_multiplier: Decimal | None = None,
    note: str | None = None,
    effective_from: date | None = None,
    source: str = "manual",
    history: CostHistory | None = None,
) -> tuple[ProductCost, bool]:
    """Новая цена — новая запись (старые не трогаем). Та же цена с той же даты — без новой записи.
    Без даты: первая цена артикула — «с начала учёта» (действует и для прошлых отчётов),
    следующие — с сегодня. Возвращает (запись, создана ли новая). Коммит — на вызывающем."""
    history = history or await load_cost_history(db, user_id)
    if effective_from is None:
        effective_from = today_msk() if history.history(offer_id) else BEGINNING
    current = history.record_at(offer_id, effective_from)
    if (  # на эту дату уже действует такая же цена — новая запись не нужна
        current is not None
        and current.effective_from <= effective_from
        and current.cost_price == cost_price
        and current.vat_multiplier == vat_multiplier
        and (current.note or None) == (note or None)
    ):
        return current, False
    record = ProductCost(
        user_id=user_id, offer_id=offer_id, cost_price=cost_price, vat_multiplier=vat_multiplier,
        effective_from=effective_from, note=note or None, source=source,
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    return record, True


@dataclass
class ArticleInfo:
    """Артикул, о котором знает сервис: из справочника, каталога Ozon или загруженных отчётов."""

    offer_id: str
    name: str | None = None
    sku: str | None = None
    in_catalog: bool = False  # есть в товарах из Ozon API
    report_qty: int = 0  # продано по видимым отчётам
    reports: set[uuid.UUID] = field(default_factory=set)


async def known_articles(db: AsyncSession, user_id: uuid.UUID, history: CostHistory) -> dict[str, ArticleInfo]:
    out: dict[str, ArticleInfo] = {offer: ArticleInfo(offer) for offer in history.offers()}
    for offer, name, sku in await db.execute(
        select(Product.offer_id, Product.name, Product.ozon_sku).where(Product.user_id == user_id)
    ):
        info = out.setdefault(offer, ArticleInfo(offer))
        info.in_catalog = True
        info.name = info.name or name
        info.sku = info.sku or (str(sku) if sku else None)
    reports = await db.execute(
        select(Report.id, Report.summary_json).where(Report.user_id == user_id, Report.deleted_at.is_(None))
    )
    for report_id, summary in reports:
        for offer, a in (summary.get("by_article") or {}).items():
            info = out.setdefault(offer, ArticleInfo(offer))
            info.name = info.name or a.get("name") or None
            info.sku = info.sku or a.get("sku") or None
            info.report_qty += int(a.get("qty") or 0)
            info.reports.add(report_id)
    return out
