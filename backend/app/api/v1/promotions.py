"""Акции Ozon: список, товары акции, товары, добавленные автоматически."""
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.price_calculator import margin_at
from app.engine.products import positive
from app.engine.promotions import LOW, NEGATIVE, OK, margin_level
from app.models.product import Product
from app.models.promotion import Promotion, PromotionProduct
from app.models.user import User
from app.schemas.promotion import (
    NO_COST_NOTE,
    ActionResponse,
    HistoryResponse,
    JoinRequest,
    LeaveRequest,
    WHOLE_STOCK_WARNING,
    AutoAddedItem,
    AutoAddedResponse,
    PromoProduct,
    PromotionProductResponse,
    PromotionProductsResponse,
    PromotionResponse,
)
from app.schemas.sync import utc
from app.services import audit, pricing
from app.services import promotion_actions as actions
from app.services.promotion_history import MAX_DAYS
from app.services.promotion_history import history as participation_history
from app.services.sync.finance import MSK, today_msk
from app.services.auth import get_current_user
from app.services.stocks import latest_stocks
from app.services.user_settings import load_low_margin

router = APIRouter(prefix="/promotions", tags=["promotions"])

_NOT_FOUND = "Акция не найдена"

MarginOf = Callable[[PromotionProduct], Decimal | None]


class Margins:
    """Маржа в акции на момент запроса и порог «низкой маржи» пользователя (шаг 7.1)."""

    def __init__(self, of: MarginOf, low: Decimal):
        self.of, self.low = of, low

    def __call__(self, r: PromotionProduct) -> Decimal | None:
        return self.of(r)

    def level(self, margin: Decimal | None) -> str | None:
        return margin_level(margin, self.low)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _margins(db: AsyncSession, user_id: uuid.UUID) -> Margins:
    """Маржа в акции — калькулятором цены (шаг 4.2) на момент запроса: себестоимость, настройки и логистика могли
    измениться после синхронизации, сохранённая в строке — снимок на её время."""
    products = {p.id: p for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()}
    ctx = await pricing.load(db, user_id, list(products.values()))
    params: dict[uuid.UUID, object] = {}

    def margin_of(r: PromotionProduct) -> Decimal | None:
        if r.product_id not in params:
            product = products.get(r.product_id)
            params[r.product_id] = ctx.product_params(product) if product else None
        p = params[r.product_id]
        # кандидат — по цене, с которой его добавит вход по умолчанию: максимальная для участия, иначе подсказка Ozon
        price = r.action_price if r.is_active else (positive(r.max_action_price) or r.action_price)
        return margin_at(p, price) if p is not None else None

    return Margins(margin_of, await load_low_margin(db, user_id))


def is_urgent(freeze: datetime | None, now: datetime) -> bool:
    """«СРОЧНО»: заморозка сегодня или завтра (по Москве) и ещё не наступила — после неё список товаров не поменять."""
    return freeze is not None and freeze > now and freeze.astimezone(MSK).date() <= now.astimezone(MSK).date() + timedelta(days=1)


def _promotion(p: Promotion, rows: list[PromotionProduct], margin_of: Margins) -> PromotionResponse:
    active = [r for r in rows if r.is_active]
    margins = [m for r in active if (m := margin_of(r)) is not None]
    levels = [margin_of.level(m) for m in margins]
    freeze = utc(p.freeze_date)
    return PromotionResponse(
        avg_margin=(sum(margins) / len(margins)).quantize(Decimal("0.01")) if margins else None,
        profitable=levels.count(OK), low_margin=levels.count(LOW), loss=levels.count(NEGATIVE),
        no_margin=len(active) - len(margins), urgent=is_urgent(freeze, _now()),
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


def _product_row(r: PromotionProduct, product: Product, on_hand: int, margin: Decimal | None, low: Decimal) -> dict:
    discount = None
    if r.price and r.action_price is not None and r.price > 0:
        discount = ((r.price - r.action_price) / r.price * 100).quantize(Decimal("0.1"))
    return dict(
        product=PromoProduct(id=product.id, offer_id=product.offer_id, name=product.name, ozon_sku=product.ozon_sku),
        action_price=r.action_price, price=r.price, discount_pct=discount, min_price=r.min_price,
        max_action_price=r.max_action_price,
        below_min_price=bool(r.min_price and r.action_price is not None and r.action_price < r.min_price),
        stock=r.stock, stock_on_hand=on_hand, is_active=r.is_active, added_automatically=r.added_automatically,
        add_mode=r.add_mode, margin_in_promo=margin, margin_level=margin_level(margin, low),
        first_seen_at=utc(r.first_seen_at),
    )


async def _on_hand(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    totals: dict[uuid.UUID, int] = defaultdict(int)
    for stock, product, _ in await latest_stocks(db, user_id):
        totals[product.id] += stock.present
    return totals


def _margin_note(rows: list[PromotionProduct], margin_of: Margins) -> str | None:
    return NO_COST_NOTE if any(margin_of(r) is None for r in rows if r.is_active) else None


async def _rows_by_promotion(db: AsyncSession, promo_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PromotionProduct]]:
    out: dict[uuid.UUID, list[PromotionProduct]] = defaultdict(list)
    if promo_ids:
        res = await db.execute(select(PromotionProduct).where(
            PromotionProduct.promotion_id.in_(promo_ids), PromotionProduct.left_at.is_(None)
        ))
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
    margin_of = await _margins(db, user.id)
    return [_promotion(p, rows[p.id], margin_of) for p in promos]


@router.get("/auto-added", response_model=AutoAddedResponse)
async def auto_added(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Товары, которые участвуют в текущих акциях без действия пользователя."""
    res = await db.execute(
        select(PromotionProduct, Product, Promotion)
        .join(Product, Product.id == PromotionProduct.product_id)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .where(
            Promotion.user_id == user.id, PromotionProduct.is_active.is_(True), PromotionProduct.left_at.is_(None),
            PromotionProduct.added_automatically.is_(True),
            or_(Promotion.date_to.is_(None), Promotion.date_to >= _now()),
        )
        .order_by(Promotion.date_to, Product.offer_id)
    )
    found = res.all()
    rows = await _rows_by_promotion(db, list({p.id for _, _, p in found}))
    on_hand = await _on_hand(db, user.id)
    margin_of = await _margins(db, user.id)
    items = [
        AutoAddedItem(
            promotion=_promotion(promo, rows[promo.id], margin_of),
            **_product_row(r, product, on_hand.get(product.id, 0), margin_of(r), margin_of.low),
        )
        for r, product, promo in found
    ]
    return AutoAddedResponse(margin_note=_margin_note([r for r, _, _ in found], margin_of), items=items)


@router.get("/history", response_model=HistoryResponse)
async def promotion_history(
    product_id: uuid.UUID | None = Query(None, description="Товар; без него — все товары"),
    date_from: date | None = Query(None, description="По умолчанию — 90 дней назад"),
    date_to: date | None = Query(None, description="По умолчанию — сегодня"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """В каких акциях был товар: когда вошёл и вышел, по какой цене (со всеми изменениями), добавлен ли
    автоматически. История копится с первой синхронизации акций — прошлого Ozon не отдаёт."""
    end = date_to or today_msk()
    start = date_from or end - timedelta(days=89)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Период: не больше {MAX_DAYS} дней за раз")
    if product_id is not None:
        product = await db.get(Product, product_id)
        if product is None or product.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    items = await participation_history(db, user.id, start, end, product_id)
    return {"date_from": start, "date_to": end, "items": items}


@router.get("/{promotion_id}", response_model=PromotionResponse)
async def get_promotion(promotion_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    promo = await db.get(Promotion, promotion_id)
    if promo is None or promo.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    rows = await _rows_by_promotion(db, [promo.id])
    return _promotion(promo, rows[promo.id], await _margins(db, user.id))


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
        .where(PromotionProduct.promotion_id == promo.id, PromotionProduct.left_at.is_(None))
        .order_by(PromotionProduct.is_active.desc(), Product.offer_id)
    )
    pairs = res.all()
    all_rows = [r for r, _ in pairs]
    on_hand = await _on_hand(db, user.id)
    margin_of = await _margins(db, user.id)
    items = [
        PromotionProductResponse(**_product_row(r, product, on_hand.get(product.id, 0), margin_of(r), margin_of.low))
        for r, product in pairs
        if participating is None or r.is_active == participating
    ]
    # самая низкая маржа — первой, как раньше по сохранённой (участники выше кандидатов)
    items.sort(key=lambda i: (not i.is_active, i.margin_in_promo is None, i.margin_in_promo or 0))
    return PromotionProductsResponse(
        promotion=_promotion(promo, all_rows, margin_of), margin_note=_margin_note(all_rows, margin_of), items=items
    )


@router.post("/{promotion_id}/products/{product_id}/join", response_model=ActionResponse)
async def join_promotion(
    promotion_id: uuid.UUID,
    product_id: uuid.UUID,
    data: JoinRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Добавить товар в акцию (или изменить его цену и количество в ней). Без confirm — предпросмотр: проверки, маржа,
    предупреждения; в Ozon не ходим. С confirm=true — те же проверки заново, запрос в Ozon, запись в журнал."""
    if not data.confirm:
        audit.not_a_change(request)  # предпросмотр ничего не меняет
    try:
        t = await actions.load_target(db, user.id, promotion_id, product_id)
        if not data.confirm:
            return await actions.join_preview(db, user.id, t, data.action_price, data.stock)
        return await actions.apply_join(db, user.id, t, data.action_price, data.stock, data.accept_loss, request)
    except actions.ActionError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.post("/{promotion_id}/products/{product_id}/leave", response_model=ActionResponse)
async def leave_promotion(
    promotion_id: uuid.UUID,
    product_id: uuid.UUID,
    data: LeaveRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Убрать товар из акции. Без confirm — предпросмотр; с confirm=true — запрос в Ozon и запись в журнал."""
    if not data.confirm:
        audit.not_a_change(request)  # предпросмотр ничего не меняет
    try:
        t = await actions.load_target(db, user.id, promotion_id, product_id)
        if not data.confirm:
            return await actions.leave_preview(db, user.id, t)
        return await actions.apply_leave(db, user.id, t, request)
    except actions.ActionError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
