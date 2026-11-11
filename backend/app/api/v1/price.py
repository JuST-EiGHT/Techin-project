"""Цены (шаг 4.2): калькулятор минимальной и целевой цены, таблица цен, подсказки «что поднять».

Целевая маржа — настройка пользователя (PATCH /settings); реклама, возвраты и хранение — по начислениям Ozon (4.2a),
настройка «реклама %» — когда данных нет. Здесь — только расчёт, ничего в Ozon не меняется.
"""
from typing import Literal

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.products import matches
from app.models.product import Product
from app.models.user import User
from app.models.price_extras import ProductPriceExtras
from app.models.user_settings import UserSettings
from app.schemas.pricing import (
    AllExtras, CalculateRequest, CalculateResponse, ExtrasUpdate, PricesPage, SavedExtras, Suggestions,
)
from app.services import audit
from app.services import pricing as svc
from app.services.stores import get_current_store
from app.services.user_settings import defaults, get_settings, price_all

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
                     "defaults": s.price_ad_pct is None and s.price_target_margin_pct is None, "for_all": ctx.for_all},
        "tax": _tax(ctx), "shop_logistics": {
            "per_unit": ctx.shop.per_unit, "text": ctx.shop.text, "returns": ctx.shop_facts.returns,
            "storage": ctx.shop_facts.storage, "ads_pct": ctx.shop_facts.ads_pct,
            "unattributed_ads_pct": ctx.shop_facts.unattributed_ads_pct if ctx.shop_facts.days else None,
            "storage_by_report": ctx.shop_facts.storage_by_report,
        },
    }


@router.get("/suggestions", response_model=Suggestions)
async def suggestions(
    limit: int = Query(5, ge=1, le=50), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Самые убыточные (цена ниже минимальной) и недооценённые (ниже целевой) товары в продаже."""
    rows, _ = await svc.table(db, user.id)
    return svc.suggestions(rows, limit)


@router.put("/extras/{product_id}", response_model=SavedExtras | None)
async def save_extras(
    product_id: uuid.UUID, data: ExtrasUpdate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Свои расходы товара — хранение и прочее на 1 шт., доп. расходы и реклама в % — для всех расчётов цены: таблицы цен
    и товаров, маржа в акциях, подсказки. Пустое поле — убрать своё значение (останется факт по начислениям или общее).
    Ответ — что сохранено."""
    product = await db.get(Product, product_id)
    if product is None or product.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден")
    row = await db.scalar(select(ProductPriceExtras).where(
        ProductPriceExtras.user_id == user.id, ProductPriceExtras.product_id == product.id))
    if row is None:
        row = ProductPriceExtras(user_id=user.id, product_id=product.id)
        db.add(row)
    row.storage, row.other, row.extra_pct, row.ad_pct = data.storage, data.other, data.extra_pct, data.ad_pct
    row.note = (data.note or "").strip() or None
    await db.commit()
    if all(v is None for v in (row.storage, row.other, row.extra_pct, row.ad_pct)):
        return None
    return SavedExtras(storage=row.storage, other=row.other, extra_pct=row.extra_pct, ad_pct=row.ad_pct, note=row.note)


@router.put("/extras-all", response_model=AllExtras)
async def save_extras_all(data: ExtrasUpdate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Расходы для всех товаров (правка 11.11.2026): реклама, % от цены, и хранение на 1 шт. — вместо начислений Ozon,
    прочее на 1 шт. и доп. расходы, % — сверху. Своё значение у товара важнее. Пустое поле — убрать (реклама и
    хранение снова по начислениям, прочее и доп. — 0). Ответ — что задано."""
    s = await db.get(UserSettings, user.id)
    if s is None:
        s = defaults(user.id)
        db.add(s)
    s.price_ad_all_pct, s.price_storage_rub = data.ad_pct, data.storage
    s.price_other_rub, s.price_extra_pct = data.other, data.extra_pct
    await db.commit()
    return AllExtras(**price_all(s))
