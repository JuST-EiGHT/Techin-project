"""Цены (шаг 4.2): входные данные калькулятора по товару и расчёт — для страницы цен, панели товара, товаров и акций.

Откуда что (правило 13 — данные продавца, а не владельца):
- себестоимость и её НДС — справочник на сегодня (2.3), НДС поставщика по умолчанию — налоговый профиль;
- комиссия FBO и эквайринг — последний снимок цен Ozon (1.2);
- логистика на 1 шт. — фактические начисления по товару (3.4: логистика / проданные штуки, с обратной логистикой)
  за 30 дней, товар не продавался — за 90; не продавался и тогда — средняя по магазину (оценка); данных нет совсем —
  «нет данных», а не 0 (правило 12): Ozon в ценах отдаёт только последнюю милю, без логистики FBO;
- реклама и целевая маржа — настройки пользователя; налог и НДС — налоговый профиль на сегодня.
Чего не хватает — расчёта нет, а в ответе сказано, что указать.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import price_calculator as calc
from app.engine.products import positive
from app.models.product import Product, ProductPrice
from app.models.promotion import Promotion, PromotionProduct
from app.services import product_pnl
from app.services.costs import CostHistory, load_cost_history
from app.services.pnl_service import PnLRequest
from app.services.sync.finance import today_msk
from app.services.sync.products import latest_prices
from app.services.tax_profile import load_profiles, profile_on, supplier_vat_multiplier
from app.services.user_settings import get_settings, price_settings

WINDOWS = (30, 90)  # дней продаж для логистики на штуку
CENT = Decimal("0.01")

MISSING_COST = "Нет себестоимости — укажите её в карточке товара или в справочнике."
MISSING_COMMISSION = "Нет комиссии Ozon — цены товара ещё не загружались из Ozon."
MISSING_LOGISTICS = "Нет данных о логистике: продаж ещё не было — укажите логистику на 1 шт. вручную."


@dataclass(frozen=True)
class Logistics:
    per_unit: Decimal | None
    source: str | None = None  # product | shop | manual | None — нет данных
    days: int | None = None
    qty: int = 0

    @property
    def text(self) -> str:
        if self.source == "product":
            return f"по продажам товара за {self.days} дн. ({self.qty} шт.), с обратной логистикой"
        if self.source == "shop":
            return f"средняя по магазину за {self.days} дн. — товар не продавался, оценка"
        if self.source == "manual":
            return "указана вручную"
        return "нет данных"


@dataclass(frozen=True)
class Overrides:
    """Свои значения для расчёта — не сохраняются (реклама и маржа сохраняются отдельно, в настройках)."""

    cost: Decimal | None = None
    commission_pct: Decimal | None = None
    acquiring_pct: Decimal | None = None
    ad_pct: Decimal | None = None
    logistics: Decimal | None = None
    storage: Decimal | None = None
    other: Decimal | None = None
    target_margin_pct: Decimal | None = None
    price: Decimal | None = None
    promo_price: Decimal | None = None


@dataclass
class Inputs:
    """Входные данные товара и откуда они: что подставлено из Ozon, справочника, настроек — и чего нет."""

    cost: Decimal | None
    supplier_vat: Decimal
    commission_pct: Decimal | None
    acquiring_pct: Decimal
    ad_pct: Decimal
    logistics: Logistics
    storage: Decimal
    other: Decimal
    target_margin_pct: Decimal
    price: Decimal | None
    sources: dict[str, str] = field(default_factory=dict)  # поле → «из Ozon», «из справочника», «ваше» …
    missing: list[str] = field(default_factory=list)


@dataclass
class PricingContext:
    """Всё для расчёта цен пользователя — загружается один раз на запрос."""

    today: date
    ad_pct: Decimal
    target_margin_pct: Decimal
    tax: calc.UnitTax
    default_vat: Decimal
    history: CostHistory
    prices: dict[uuid.UUID, ProductPrice]
    logistics: dict[str, Logistics]
    shop: Logistics

    def logistics_for(self, offer_id: str) -> Logistics:
        return self.logistics.get(offer_id) or self.shop

    def inputs(self, product: Product, ov: Overrides | None = None) -> Inputs:
        ov = ov or Overrides()
        snap = self.prices.get(product.id)
        record = self.history.record_at(product.offer_id, self.today)
        sources: dict[str, str] = {}

        def pick(name: str, mine, found, found_source: str):
            if mine is not None:
                sources[name] = "ваше значение"
                return mine
            if found is not None:
                sources[name] = found_source
            return found

        cost = pick("cost", ov.cost, Decimal(record.cost_price) if record else None, "из справочника себестоимости")
        vat = Decimal(record.vat_multiplier) if record and record.vat_multiplier is not None else self.default_vat
        commission = pick("commission_pct", ov.commission_pct, snap.commission_pct if snap else None, "из Ozon (FBO)")
        acquiring = pick("acquiring_pct", ov.acquiring_pct, snap.acquiring_pct if snap else None, "из Ozon")
        ad = pick("ad_pct", ov.ad_pct, self.ad_pct, "ваша настройка")
        margin = pick("target_margin_pct", ov.target_margin_pct, self.target_margin_pct, "ваша настройка")
        price = pick("price", ov.price, positive(snap.price) if snap else None, "текущая цена в Ozon")
        logistics = (
            Logistics(ov.logistics, "manual") if ov.logistics is not None else self.logistics_for(product.offer_id)
        )
        missing = [
            text for text, absent in (
                (MISSING_COST, cost is None), (MISSING_COMMISSION, commission is None),
                (MISSING_LOGISTICS, logistics.per_unit is None),
            ) if absent
        ]
        return Inputs(
            cost=cost, supplier_vat=vat, commission_pct=commission, acquiring_pct=acquiring or Decimal(0), ad_pct=ad,
            logistics=logistics, storage=ov.storage or Decimal(0), other=ov.other or Decimal(0),
            target_margin_pct=margin, price=price, sources=sources, missing=missing,
        )

    def params(self, inputs: Inputs) -> calc.PriceParams | None:
        if inputs.missing:
            return None
        return calc.PriceParams(
            cost=inputs.cost, supplier_vat=inputs.supplier_vat, commission_pct=inputs.commission_pct,
            acquiring_pct=inputs.acquiring_pct, ad_pct=inputs.ad_pct, logistics=inputs.logistics.per_unit,
            storage=inputs.storage, other=inputs.other, target_margin_pct=inputs.target_margin_pct, tax=self.tax,
        )

    def product_params(self, product: Product) -> calc.PriceParams | None:
        return self.params(self.inputs(product))


async def load_logistics(
    db: AsyncSession, user_id: uuid.UUID, offers: set[str], today: date
) -> tuple[dict[str, Logistics], Logistics]:
    """Логистика на проданную штуку по товарам и в среднем по магазину: 30 дней, кому не хватило — 90."""
    by_offer: dict[str, Logistics] = {}
    shop = Logistics(None)
    for days in WINDOWS:
        if offers <= set(by_offer) and shop.per_unit is not None:
            break
        data = await product_pnl.compute(db, user_id, PnLRequest(today - timedelta(days=days), today - timedelta(days=1)))
        for item in data["items"]:
            if item["qty"] > 0 and item["offer_id"] not in by_offer:
                by_offer[item["offer_id"]] = Logistics(_per_unit(item["logistics"], item["qty"]), "product", days, item["qty"])
        totals = data["totals"]
        if shop.per_unit is None and totals["qty"] > 0:
            shop = Logistics(_per_unit(totals["logistics"], totals["qty"]), "shop", days, totals["qty"])
    return by_offer, shop


def _per_unit(logistics: str, qty: int) -> Decimal:
    """Логистика в начислениях — со знаком минус; на штуку — расход, не меньше 0."""
    return max(-Decimal(logistics) / qty, Decimal(0)).quantize(CENT, ROUND_HALF_UP)


async def load(db: AsyncSession, user_id: uuid.UUID, products: list[Product] | None = None) -> PricingContext:
    if products is None:
        products = list((await db.execute(select(Product).where(Product.user_id == user_id))).scalars())
    today = today_msk()
    row = profile_on(await load_profiles(db, user_id), today)
    ad, margin = price_settings(await get_settings(db, user_id))
    by_offer, shop = await load_logistics(db, user_id, {p.offer_id for p in products}, today)
    return PricingContext(
        today=today, ad_pct=ad, target_margin_pct=margin,
        tax=calc.unit_tax(row.to_engine() if row else None, today),
        default_vat=await supplier_vat_multiplier(db, user_id, today), history=await load_cost_history(db, user_id),
        prices=await latest_prices(db, [p.id for p in products]), logistics=by_offer, shop=shop,
    )


async def current_promotions(
    db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID | None = None
) -> dict[uuid.UUID, list[tuple[PromotionProduct, Promotion]]]:
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
    out: dict[uuid.UUID, list[tuple[PromotionProduct, Promotion]]] = {}
    for r, promo in await db.execute(q):
        out.setdefault(r.product_id, []).append((r, promo))
    return out


# ── расчёт товара: панель и POST /price/calculate ──
def _breakdown(b: calc.Breakdown | None) -> dict | None:
    return None if b is None else vars(b).copy()


def calculate(ctx: PricingContext, product: Product, ov: Overrides | None = None, promo_price: Decimal | None = None) -> dict:
    inputs = ctx.inputs(product, ov)
    params = ctx.params(inputs)
    promo = ov.promo_price if ov and ov.promo_price is not None else promo_price
    result = None
    if params is not None:
        r = calc.calculate(params, inputs.price, promo)
        result = {
            "min_price": r.min_price, "min_error": r.min_error, "target_price": r.target_price,
            "target_error": r.target_error, "profitable": r.profitable,
            "current": _breakdown(r.current), "promo": _breakdown(r.promo),
        }
    return {
        "inputs": {
            "cost": inputs.cost, "supplier_vat": inputs.supplier_vat,
            "cost_with_vat": None if inputs.cost is None else (inputs.cost * inputs.supplier_vat).quantize(CENT, ROUND_HALF_UP),
            "commission_pct": inputs.commission_pct, "acquiring_pct": inputs.acquiring_pct, "ad_pct": inputs.ad_pct,
            "logistics": inputs.logistics.per_unit, "logistics_source": inputs.logistics.source,
            "logistics_text": inputs.logistics.text, "storage": inputs.storage, "other": inputs.other,
            "target_margin_pct": inputs.target_margin_pct, "price": inputs.price, "promo_price": promo,
            "sources": inputs.sources,
        },
        "tax": {"title": ctx.tax.title, "known": ctx.tax.known, "notes": list(ctx.tax.notes),
                "profit_tax_pct": ctx.tax.profit_tax_pct},
        "missing": inputs.missing,
        "result": result,
    }


# ── таблица цен и подсказки ──
BELOW_MIN, BELOW_TARGET, NO_DATA, PROMO_LOSS = "below_min", "below_target", "no_data", "promo_loss"
FILTERS = (BELOW_MIN, BELOW_TARGET, NO_DATA, PROMO_LOSS)
SORTS = ("offer_id", "price", "min_price", "target_price", "margin", "promo_margin", "gap")


def table_row(ctx: PricingContext, product: Product, promos: list[tuple[PromotionProduct, Promotion]]) -> dict:
    inputs = ctx.inputs(product)
    params = ctx.params(inputs)
    price = inputs.price
    promo_rows = sorted(
        ((r, p) for r, p in promos if r.action_price is not None and r.action_price > 0), key=lambda rp: rp[0].action_price
    )
    worst = promo_rows[0] if promo_rows else None  # самая низкая цена в акциях — самая низкая маржа
    min_price = target_price = margin = profit = promo_margin = None
    errors: list[str] = []
    if params is not None:
        r = calc.calculate(params, price, worst[0].action_price if worst else None)
        min_price, target_price = r.min_price, r.target_price
        errors = [e for e in (r.min_error, r.target_error) if e]
        if r.current:
            margin, profit = r.current.margin, r.current.profit
        if r.promo:
            promo_margin = r.promo.margin
    return {
        "id": product.id, "offer_id": product.offer_id, "name": product.name, "ozon_sku": product.ozon_sku,
        "is_archived": product.is_archived, "price": price, "min_price": min_price, "target_price": target_price,
        "margin": margin, "unit_profit": profit,
        "below_min": price is not None and min_price is not None and price < min_price,
        "below_target": price is not None and target_price is not None and min_price is not None
        and min_price <= price < target_price,
        "gap_pct": None if price is None or min_price is None or min_price <= 0
        else ((price - min_price) / min_price * 100).quantize(Decimal("0.1"), ROUND_HALF_UP),
        "promo": None if worst is None else {
            "title": worst[1].title, "action_price": worst[0].action_price, "margin": promo_margin,
            "count": len(promo_rows), "added_automatically": worst[0].added_automatically,
        },
        "logistics": inputs.logistics.per_unit, "logistics_source": inputs.logistics.source,
        "missing": inputs.missing, "errors": errors,
    }


def passes(row: dict, filters: set[str]) -> bool:
    checks = {
        BELOW_MIN: row["below_min"],
        BELOW_TARGET: row["below_target"],
        NO_DATA: bool(row["missing"]) or row["min_price"] is None,
        PROMO_LOSS: row["promo"] is not None and row["promo"]["margin"] is not None and row["promo"]["margin"] < 0,
    }
    return all(checks[f] for f in filters)


def _sort_key(sort: str):
    if sort == "promo_margin":
        return lambda r: r["promo"]["margin"] if r["promo"] else None
    if sort == "gap":
        return lambda r: r["gap_pct"]
    if sort == "offer_id":
        return lambda r: r["offer_id"].lower()
    return lambda r: r[sort]


def sort_rows(rows: list[dict], sort: str, order: str) -> list[dict]:
    """Пустые значения — в конце в обе стороны, при равных — по артикулу."""
    key = _sort_key(sort)
    by_offer = sorted(rows, key=lambda r: r["offer_id"].lower())
    filled = [r for r in by_offer if key(r) is not None]
    return sorted(filled, key=key, reverse=order == "desc") + [r for r in by_offer if key(r) is None]


async def table(db: AsyncSession, user_id: uuid.UUID, archived: bool = False) -> tuple[list[dict], PricingContext]:
    products = list((await db.execute(
        select(Product).where(Product.user_id == user_id, Product.is_archived.is_(archived))
    )).scalars())
    ctx = await load(db, user_id, products)
    promos = await current_promotions(db, user_id)
    return [table_row(ctx, p, promos.get(p.id, [])) for p in products], ctx


def counts(rows: list[dict]) -> dict:
    return {"all": len(rows), **{f: sum(passes(r, {f}) for r in rows) for f in FILTERS}}


def suggestions(rows: list[dict], limit: int) -> dict:
    """Самые убыточные — цена ниже минимальной, хуже маржа — выше; недооценённые — цена между минимальной и
    целевой, сильнее всего отстаёт от целевой — выше. У каждой — до какой цены поднять и сколько это даст с штуки."""
    loss = sorted((r for r in rows if r["below_min"]), key=lambda r: (r["margin"], r["offer_id"]))
    under = sorted(
        (r for r in rows if r["below_target"]),
        key=lambda r: (-(r["target_price"] - r["price"]) / r["price"], r["offer_id"]),
    )

    def item(r: dict, to: Decimal) -> dict:
        return {"id": r["id"], "offer_id": r["offer_id"], "name": r["name"], "price": r["price"],
                "min_price": r["min_price"], "target_price": r["target_price"], "margin": r["margin"],
                "raise_to": to, "raise_pct": ((to - r["price"]) / r["price"] * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)}

    return {
        "loss": [item(r, r["min_price"]) for r in loss[:limit]], "loss_count": len(loss),
        "underpriced": [item(r, r["target_price"]) for r in under[:limit]], "underpriced_count": len(under),
    }
