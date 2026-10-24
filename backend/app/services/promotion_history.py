"""История участия в акциях (шаг 4.3, docs/DATA_HISTORY.md): в каких акциях был товар, когда вошёл и вышел, по какой
цене (со всеми изменениями), добавлен ли автоматически. Копится с первой синхронизации — Ozon прошлого не отдаёт.
С результатом каждого участия (10.16, `services/promotion_results`): продажи, выручка, прибыль во время против «до».
"""
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.promotion import Promotion, PromotionPriceSnapshot, PromotionProduct
from app.schemas.sync import utc
from app.services import promotion_results
from app.services.sync.finance import MSK

MAX_DAYS = 366
ACTIVE, LEFT, ENDED = "active", "left", "ended"  # участвует | вышел | акция закончилась


def bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Дни по Москве → [начало первого дня, начало дня после последнего) в UTC."""
    start = datetime.combine(date_from, time(), MSK).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time(), MSK).astimezone(timezone.utc)
    return start, end


async def history(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, product_id: uuid.UUID | None = None,
    with_results: bool = False, results_limit: int | None = None,
) -> list[dict]:
    """Участия, которые пересекаются с периодом: вошёл до его конца и не вышел до начала. Новые — первыми."""
    start, end = bounds(date_from, date_to)
    now = datetime.now(timezone.utc)
    q = (
        select(PromotionProduct, Promotion, Product)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .join(Product, Product.id == PromotionProduct.product_id)
        .where(
            Promotion.user_id == user_id, PromotionProduct.is_active.is_(True), PromotionProduct.first_seen_at < end,
            or_(PromotionProduct.left_at.is_(None), PromotionProduct.left_at >= start),
        )
        .order_by(PromotionProduct.first_seen_at.desc(), Product.offer_id)
    )
    if product_id is not None:
        q = q.where(PromotionProduct.product_id == product_id)
    found = (await db.execute(q)).all()
    snaps: dict[uuid.UUID, list[PromotionPriceSnapshot]] = defaultdict(list)
    if found:
        res = await db.execute(
            select(PromotionPriceSnapshot)
            .where(PromotionPriceSnapshot.promotion_product_id.in_([r.id for r, _, _ in found]))
            .order_by(PromotionPriceSnapshot.snapshot_at)
        )
        for s in res.scalars():
            snaps[s.promotion_product_id].append(s)
    # результат — для всех участий или первых results_limit (на странице товара видны последние 5)
    results = await promotion_results.results(db, user_id, found[:results_limit]) if with_results else {}
    out = []
    for r, promo, product in found:
        left = utc(r.left_at)
        promo_end = utc(promo.date_to)
        if left is not None:
            status = ENDED if promo_end is not None and left >= promo_end else LEFT
        else:
            status = ENDED if promo_end is not None and promo_end < now else ACTIVE
        prices = [{"at": utc(s.snapshot_at), "action_price": s.action_price, "stock": s.stock} for s in snaps[r.id]]
        out.append({
            "id": r.id,
            "promotion": {
                "id": promo.id, "title": promo.title, "action_type": promo.action_type,
                "date_from": utc(promo.date_from), "date_to": promo_end, "whole_stock": promo.whole_stock,
            },
            "product": {"id": product.id, "offer_id": product.offer_id, "name": product.name, "ozon_sku": product.ozon_sku},
            "entered_at": utc(r.first_seen_at),
            "left_at": left if left is not None else (promo_end if status == ENDED else None),
            "status": status,
            "action_price": prices[-1]["action_price"] if prices else r.action_price,
            "price": r.price,
            "prices": prices,
            "added_automatically": r.added_automatically,
            "margin_in_promo": r.margin_in_promo,
            "result": promotion_results.public(results[r.id]) if r.id in results else None,
        })
    return out
