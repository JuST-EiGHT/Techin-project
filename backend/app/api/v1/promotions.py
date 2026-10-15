"""Акции Ozon: список, товары акции, товары, добавленные автоматически."""
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.promotions import margin_level
from app.models.product import Product
from app.models.promotion import Promotion, PromotionProduct
from app.models.user import User
from app.schemas.promotion import (
    NO_COST_NOTE,
    WHOLE_STOCK_WARNING,
    AutoAddedItem,
    AutoAddedResponse,
    PromoProduct,
    PromotionProductResponse,
    PromotionProductsResponse,
    PromotionResponse,
)
from app.schemas.sync import utc
from app.services.auth import get_current_user
from app.services.stocks import latest_stocks

router = APIRouter(prefix="/promotions", tags=["promotions"])

_NOT_FOUND = "Акция не найдена"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _promotion(p: Promotion, rows: list[PromotionProduct]) -> PromotionResponse:
    active = [r for r in rows if r.is_active]
    margins = [r.margin_in_promo for r in active if r.margin_in_promo is not None]
    freeze = utc(p.freeze_date)
    return PromotionResponse(
        id=p.id, ozon_action_id=p.ozon_action_id, title=p.title, action_type=p.action_type,
        date_from=utc(p.date_from), date_to=utc(p.date_to), discount_type=p.discount_type,
        discount_value=p.discount_value, freeze_date=freeze, is_frozen=bool(freeze and freeze <= _now()),
        auto_add_dates=p.auto_add_dates, is_participating=p.is_participating,
        participating_products_count=p.participating_products_count,
        potential_products_count=p.potential_products_count,
        whole_stock=p.whole_stock, whole_stock_warning=WHOLE_STOCK_WARNING if p.whole_stock else None,
        products_active=len(active), candidates=len(rows) - len(active),
        auto_added=sum(r.added_automatically for r in active),
        min_margin=min(margins) if margins else None, synced_at=utc(p.synced_at),
    )


def _product_row(r: PromotionProduct, product: Product, on_hand: int) -> dict:
    discount = None
    if r.price and r.action_price is not None and r.price > 0:
        discount = ((r.price - r.action_price) / r.price * 100).quantize(Decimal("0.1"))
    return dict(
        product=PromoProduct(id=product.id, offer_id=product.offer_id, name=product.name, ozon_sku=product.ozon_sku),
        action_price=r.action_price, price=r.price, discount_pct=discount, min_price=r.min_price,
        max_action_price=r.max_action_price,
        below_min_price=bool(r.min_price and r.action_price is not None and r.action_price < r.min_price),
        stock=r.stock, stock_on_hand=on_hand, is_active=r.is_active, added_automatically=r.added_automatically,
        add_mode=r.add_mode, margin_in_promo=r.margin_in_promo, margin_level=margin_level(r.margin_in_promo),
        first_seen_at=utc(r.first_seen_at),
    )


async def _on_hand(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    totals: dict[uuid.UUID, int] = defaultdict(int)
    for stock, product, _ in await latest_stocks(db, user_id):
        totals[product.id] += stock.present
    return totals


def _margin_note(rows: list[PromotionProduct]) -> str | None:
    return NO_COST_NOTE if any(r.margin_in_promo is None for r in rows if r.is_active) else None


async def _rows_by_promotion(db: AsyncSession, promo_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PromotionProduct]]:
    out: dict[uuid.UUID, list[PromotionProduct]] = defaultdict(list)
    if promo_ids:
        res = await db.execute(select(PromotionProduct).where(PromotionProduct.promotion_id.in_(promo_ids)))
        for r in res.scalars():
            out[r.promotion_id].append(r)
    return out


@router.get("", response_model=list[PromotionResponse])
async def list_promotions(
    include_finished: bool = Query(False, description="Показывать закончившиеся акции"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Promotion).where(Promotion.user_id == user.id)
    if not include_finished:
        q = q.where(or_(Promotion.date_to.is_(None), Promotion.date_to >= _now()))
    promos = list((await db.execute(q.order_by(Promotion.is_participating.desc(), Promotion.date_to))).scalars())
    rows = await _rows_by_promotion(db, [p.id for p in promos])
    return [_promotion(p, rows[p.id]) for p in promos]


@router.get("/auto-added", response_model=AutoAddedResponse)
async def auto_added(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Товары, которые участвуют в текущих акциях без действия пользователя."""
    res = await db.execute(
        select(PromotionProduct, Product, Promotion)
        .join(Product, Product.id == PromotionProduct.product_id)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .where(
            Promotion.user_id == user.id, PromotionProduct.is_active.is_(True),
            PromotionProduct.added_automatically.is_(True),
            or_(Promotion.date_to.is_(None), Promotion.date_to >= _now()),
        )
        .order_by(Promotion.date_to, Product.offer_id)
    )
    found = res.all()
    rows = await _rows_by_promotion(db, list({p.id for _, _, p in found}))
    on_hand = await _on_hand(db, user.id)
    items = [
        AutoAddedItem(promotion=_promotion(promo, rows[promo.id]), **_product_row(r, product, on_hand.get(product.id, 0)))
        for r, product, promo in found
    ]
    return AutoAddedResponse(margin_note=_margin_note([r for r, _, _ in found]), items=items)


@router.get("/{promotion_id}/products", response_model=PromotionProductsResponse)
async def promotion_products(
    promotion_id: uuid.UUID,
    participating: bool | None = Query(None, description="true — участвуют, false — кандидаты, без параметра — все"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    promo = await db.get(Promotion, promotion_id)
    if promo is None or promo.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    res = await db.execute(
        select(PromotionProduct, Product)
        .join(Product, Product.id == PromotionProduct.product_id)
        .where(PromotionProduct.promotion_id == promo.id)
        .order_by(PromotionProduct.is_active.desc(), PromotionProduct.margin_in_promo.asc().nulls_last(), Product.offer_id)
    )
    pairs = res.all()
    all_rows = [r for r, _ in pairs]
    on_hand = await _on_hand(db, user.id)
    items = [
        PromotionProductResponse(**_product_row(r, product, on_hand.get(product.id, 0)))
        for r, product in pairs
        if participating is None or r.is_active == participating
    ]
    return PromotionProductsResponse(promotion=_promotion(promo, all_rows), margin_note=_margin_note(all_rows), items=items)
