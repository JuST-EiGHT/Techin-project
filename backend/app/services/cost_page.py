"""Себестоимость (шаг 10.15): привязки артикулов файла к товарам Ozon и полнота — у каких товаров с продажами
себестоимости нет и какая доля выручки без неё (правило 5: неизвестная себестоимость — не 0, а пробел в данных).

Полнота — по «Прибыли по товарам» (3.4) за период: те же продажи и тот же признак «нет себестоимости», что в P&L и
аналитике. За период без начислений продажи неизвестны — доли нет, а не 0.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.offer_match import normalize
from app.models.offer_alias import OfferAlias
from app.models.product import Product
from app.services import product_pnl
from app.services.cost_import import CostImportError, active_aliases
from app.services.costs import load_cost_history
from app.services.pnl_service import PnLRequest
from app.services.sync.finance import today_msk

COVERAGE_DAYS = 30
SHOWN = 50


async def _names(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Product]:
    rows = await db.execute(select(Product).where(Product.user_id == user_id))
    return {p.offer_id: p for p in rows.scalars()}


# ── привязки ──
async def list_aliases(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    products = await _names(db, user_id)
    aliases = sorted((await active_aliases(db, user_id)).values(), key=lambda a: a.alias.casefold())
    return [{"id": a.id, "alias": a.alias, "offer_id": a.offer_id,
             "name": products[a.offer_id].name if a.offer_id in products else None, "created_at": a.created_at}
            for a in aliases]


async def bind(db: AsyncSession, user_id: uuid.UUID, alias: str, offer_id: str) -> OfferAlias:
    """Привязать артикул файла к товару Ozon. Прежняя привязка того же артикула — снимается (removed_at)."""
    alias, offer_id = alias.strip(), offer_id.strip()
    key = normalize(alias)
    if not key:
        raise CostImportError("Артикул из файла пустой — привязывать нечего.")
    products = await _names(db, user_id)
    if offer_id not in products:
        raise CostImportError(f"Товара с артикулом {offer_id} нет в каталоге Ozon этого магазина.")
    if alias in products:
        raise CostImportError(f"Артикул {alias} и так есть в каталоге Ozon — привязка не нужна.")
    now = datetime.now(timezone.utc)
    if (old := (await active_aliases(db, user_id)).get(key)) is not None:
        old.removed_at = now
    record = OfferAlias(user_id=user_id, alias=alias, alias_key=key, offer_id=offer_id, created_at=now)
    db.add(record)
    await db.commit()
    return record


async def unbind(db: AsyncSession, user_id: uuid.UUID, alias_id: uuid.UUID) -> bool:
    record = await db.get(OfferAlias, alias_id)
    if record is None or record.user_id != user_id or record.removed_at is not None:
        return False
    record.removed_at = datetime.now(timezone.utc)
    await db.commit()
    return True


# ── полнота ──
def _share(part: Decimal, total: Decimal) -> Decimal | None:
    return (part / total * 100).quantize(Decimal("0.1"), ROUND_HALF_UP) if total > 0 else None


async def coverage(db: AsyncSession, user_id: uuid.UUID, days: int = COVERAGE_DAYS) -> dict:
    today = today_msk()
    start = today - timedelta(days=days - 1)
    data = await product_pnl.compute(db, user_id, PnLRequest(start, today))
    products = await _names(db, user_id)
    history = await load_cost_history(db, user_id)
    selling = [p for p in products.values() if not p.is_archived]
    sold = [i for i in data["items"] if i["qty"] > 0]
    missing = sorted((i for i in sold if i["cost_missing"]), key=lambda i: (-Decimal(i["revenue"]), i["offer_id"]))
    known = data["data_source"] != "none"
    revenue = sum((Decimal(i["revenue"]) for i in data["items"]), Decimal(0)) if known else None
    revenue_missing = sum((Decimal(i["revenue"]) for i in missing), Decimal(0)) if known else None
    return {
        "date_from": start, "date_to": today, "data_source": data["data_source"],
        "catalog_total": len(selling),
        "catalog_with_cost": sum(1 for p in selling if history.record_at(p.offer_id, today) is not None),
        "sold_total": len(sold), "sold_without_cost": len(missing),
        "revenue_total": revenue, "revenue_without_cost": revenue_missing,
        "revenue_share_without_cost": _share(revenue_missing, revenue) if known else None,
        "items": [
            {"offer_id": i["offer_id"], "name": i["name"] or (products[i["offer_id"]].name if i["offer_id"] in products else None),
             "product_id": products[i["offer_id"]].id if i["offer_id"] in products else None,
             "qty": i["qty"], "revenue": Decimal(i["revenue"]), "share": _share(Decimal(i["revenue"]), revenue or Decimal(0))}
            for i in missing[:SHOWN]
        ],
    }
