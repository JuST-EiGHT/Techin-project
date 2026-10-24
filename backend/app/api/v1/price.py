"""Цены (шаг 4.2): калькулятор минимальной и целевой цены, таблица цен, подсказки «что поднять».

Реклама и целевая маржа — настройки пользователя (PATCH /settings); здесь — только расчёт, ничего в Ozon не меняется.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.products import matches
from app.models.product import Product
from app.models.user import User
from app.schemas.pricing import CalculateRequest, CalculateResponse, PricesPage, Suggestions
from app.services import audit
from app.services import pricing as svc
from app.services.stores import get_current_store
from app.services.user_settings import get_settings

router = APIRouter(prefix="/price", tags=["price"])

Sort = Literal[svc.SORTS]  # type: ignore[valid-type]

_FILTER_HELP = (
    "Через запятую, товар подходит под все: below_min — цена ниже минимальной, below_target — между минимальной и "
    "целевой, no_data — не хватает данных для расчёта, promo_loss — в акции в минус"
)


def _tax(ctx: svc.PricingContext) -> dict:
    return {"title": ctx.tax.title, "known": ctx.tax.known, "notes": list(ctx.tax.notes),
            "profit_tax_pct": ctx.tax.profit_tax_pct}


@router.post("/calculate", response_model=CalculateResponse, dependencies=[Depends(audit.read_only)])
async def calculate(data: CalculateRequest, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Минимальная и целевая цена товара, маржа при текущей цене и в акции. Переданные поля заменяют данные Ozon,
    справочника и настроек только для этого расчёта."""
    product = await db.get(Product, data.product_id)
    if product is None or product.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    ctx = await svc.load(db, user.id, [product])
    promos = (await svc.current_promotions(db, user.id, product.id)).get(product.id, [])
    priced = sorted((rp for rp in promos if rp[0].action_price), key=lambda rp: rp[0].action_price)
    ov = svc.Overrides(**data.model_dump(exclude={"product_id", "what_if"}))
    worst = priced[0] if priced and data.promo_price is None else None
    out = svc.calculate(ctx, product, ov, worst[0].action_price if worst else None, what_if=data.what_if)
    return {
        **out, "product": {"id": product.id, "offer_id": product.offer_id, "name": product.name},
        "tax": _tax(ctx), "promo_title": worst[1].title if worst else None,
    }


@router.get("", response_model=PricesPage)
async def price_table(
    search: str | None = Query(None, max_length=100, description="Часть артикула, названия или SKU"),
    filters: str | None = Query(None, description=_FILTER_HELP),
    archived: bool = Query(False),
    sort: Sort = Query("offer_id"),
    order: Literal["asc", "desc"] = Query("asc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Цена, минимальная и целевая, маржа и маржа в акции по каждому товару. Счётчики — без поиска и фильтров."""
    chosen = {f.strip() for f in (filters or "").split(",") if f.strip()}
    if unknown := chosen - set(svc.FILTERS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Неизвестный фильтр: {', '.join(sorted(unknown))}")
    rows, ctx = await svc.table(db, user.id, archived)
    counts = svc.counts(rows)
    q = (search or "").strip()
    found = [r for r in rows if (not q or matches(q, r["offer_id"], r["name"], r["ozon_sku"])) and svc.passes(r, chosen)]
    found = svc.sort_rows(found, sort, order)
    s = await get_settings(db, user.id)
    return {
        "items": found[offset:offset + limit], "total": len(found), "limit": limit, "offset": offset, "counts": counts,
        "settings": {"ad_pct": ctx.ad_pct, "target_margin_pct": ctx.target_margin_pct,
                     "defaults": s.price_ad_pct is None and s.price_target_margin_pct is None},
        "tax": _tax(ctx), "shop_logistics": {"per_unit": ctx.shop.per_unit, "text": ctx.shop.text},
    }


@router.get("/suggestions", response_model=Suggestions)
async def suggestions(
    limit: int = Query(5, ge=1, le=50), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Самые убыточные (цена ниже минимальной) и недооценённые (ниже целевой) товары в продаже."""
    rows, _ = await svc.table(db, user.id)
    return svc.suggestions(rows, limit)
