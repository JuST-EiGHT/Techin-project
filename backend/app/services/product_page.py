"""«Статистика товара» (шаг 10.14, docs/SITEMAP.md п. 3.2): всё о товаре на одной странице.

Своих таблиц и расчётов нет — собрано из готового (правило 12: итоги производные):
- карточка — панель товара 4.1 (`products.detail_parts`): цена за 30 дней, остатки по складам, себестоимость, значки 10.13;
- продажи, выручка, прибыль и юнит-экономика за период — «Прибыль по товарам» (3.4, `per_unit` 3.7a) — те же цифры,
  что в «Аналитике» (10.3, 10.9); за период без начислений — нет данных, а не 0;
- цены и скидки — калькулятор 4.2: зачёркнутая цена → ваша цена → с акциями продавца → цена в каждой текущей акции
  (и в акциях, куда товар можно добавить), маржа и прибыль с 1 шт. в каждом случае и насколько она меньше, чем при
  вашей цене. Цену для покупателя со скидкой за счёт Ozon (`customer_price`) Ozon отдаёт только с подпиской Premium Pro;
- остатки по кластерам и своим складам — последние снимки (1.3), поставка и срочность — «Поставки» (5.2), риск
  дефицита — срочность поставки, иначе статус остатка по порогам 5.1;
- история: себестоимость (2.3), изменения цены через сервис (6.4), участие в акциях (4.3) — последние записи за год.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import price_calculator as calc
from app.engine.anomaly_text import num
from app.engine.products import positive
from app.models.product import Product
from app.models.promotion import Promotion, PromotionProduct
from app.schemas.sync import utc
from app.services import price_history, product_pnl, products, supply
from app.services.pnl_service import PnLRequest
from app.services.promotion_history import history as participation_history

HISTORY_DAYS = 365  # история цен и акций на странице — за год, последние записи
HISTORY_SHOWN = 5
URGENCY_RANK = {"critical": 0, "urgent": 1, "planned": 2}
PRICE_NOTE = (
    "Маржа и прибыль с 1 шт. — калькулятор цены: комиссия, эквайринг, реклама, логистика, себестоимость с НДС, налоги "
    "по профилю; до налога на прибыль. Цену для покупателя со скидкой за счёт Ozon Ozon отдаёт только продавцам с "
    "подпиской Premium Pro — её здесь нет."
)


async def stats(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID, date_from: date, date_to: date) -> dict | None:
    product = await db.get(Product, product_id)
    if product is None or product.user_id != user_id:
        return None
    detail, ctx = await products.detail_parts(db, user_id, product, 30, (date_from, date_to))
    rows, period = await products.load_period(db, user_id, date_from, date_to)
    pnl_data = await product_pnl.compute(db, user_id, PnLRequest(date_from, date_to))
    report = await supply.report(db, user_id, await supply.load_params(db, user_id))
    own = [i for i in report["items"] if i["product"]["id"] == product.id]
    clusters = stock_clusters(detail["stocks"], own)
    today = ctx.today
    changes = await price_history.history(
        db, user_id, today - timedelta(days=HISTORY_DAYS - 1), today, product_id=product.id, limit=HISTORY_SHOWN,
    )
    promos = await participation_history(db, user_id, today - timedelta(days=HISTORY_DAYS - 1), today, product.id)
    return {
        "detail": detail,
        "period": period,
        "pnl": rows.get(product.offer_id),
        "cost_basis": pnl_data["cost_basis"],
        "prices": await price_levels(db, ctx, product),
        "clusters": clusters,
        "supply_known": report["postings_known"],
        "risk": risk(detail["item"], own),
        "cost_history": [
            {"cost_price": r.cost_price.quantize(Decimal("0.01")), "vat_multiplier": r.vat_multiplier,
             "effective_from": r.effective_from, "note": r.note, "source": r.source, "created_at": r.created_at}
            for r in reversed(ctx.pricing.history.history(product.offer_id))  # новые — первыми
        ],
        "price_changes": changes["items"],
        "price_changes_total": changes["total"],
        "promotions": promos[:HISTORY_SHOWN],
        "promotions_total": len(promos),
    }


# ── цены и скидки ──
def _pct_off(regular: Decimal | None, value: Decimal) -> Decimal | None:
    if regular is None or regular <= 0 or value >= regular:
        return None
    return ((regular - value) / regular * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


async def price_levels(db: AsyncSession, ctx: products.Context, product: Product) -> dict:
    """Цена → со скидками → в акциях: маржа и прибыль с 1 шт. в каждом случае (калькулятор 4.2)."""
    pc = ctx.pricing
    inputs = pc.inputs(product)
    params = pc.params(inputs)
    snap = pc.prices.get(product.id)
    price = positive(snap.price) if snap else None
    regular = calc.breakdown(params, price) if params is not None and price is not None else None
    levels: list[dict] = []

    def add(kind: str, title: str, value: Decimal, **extra) -> None:
        b = calc.breakdown(params, value) if params is not None and kind != "old" else None
        levels.append({
            "kind": kind, "title": title, "price": value, "discount_pct": _pct_off(price, value),
            "margin": b.margin if b else None, "unit_profit": b.profit if b else None,
            "profit_change": b.profit - regular.profit if b and regular and kind != "price" else None,
            "promotion_id": None, "date_to": None, "added_automatically": False, **extra,
        })

    old = positive(snap.old_price) if snap else None
    if old is not None and price is not None and old > price:
        add("old", "Зачёркнутая — до скидки", old)
    if price is not None:
        add("price", "Ваша цена", price)
    marketing = positive(snap.marketing_price) if snap else None
    if marketing is not None and marketing != price:
        add("marketing", "С вашими акциями", marketing)
    for r, promo in ctx.promos.get(product.id, []):
        if positive(r.action_price) is not None:
            add("promo", promo.title, r.action_price, promotion_id=promo.id, date_to=utc(promo.date_to),
                added_automatically=r.added_automatically)
    for r, promo in await candidates(db, product):
        value = positive(r.max_action_price) or positive(r.action_price)
        if value is not None:
            add("candidate", promo.title, value, promotion_id=promo.id, date_to=utc(promo.date_to))
    return {"levels": levels, "missing": inputs.missing, "note": PRICE_NOTE}


async def candidates(db: AsyncSession, product: Product) -> list[tuple[PromotionProduct, Promotion]]:
    """Текущие акции, куда товар можно добавить: кандидат по последней синхронизации, акция не закончилась."""
    rows = await db.execute(
        select(PromotionProduct, Promotion)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .where(
            Promotion.user_id == product.user_id, PromotionProduct.product_id == product.id,
            PromotionProduct.is_active.is_(False), PromotionProduct.left_at.is_(None),
            or_(Promotion.date_to.is_(None), Promotion.date_to >= datetime.now(timezone.utc)),
        )
        .order_by(Promotion.date_to, Promotion.title)
    )
    return [(r, p) for r, p in rows]


# ── остатки, поставки, риск дефицита ──
def stock_clusters(stocks: list[dict], supply_items: list[dict]) -> list[dict]:
    """Остатки по кластерам Ozon и своим складам; к каждому — поставка или закупка из «Поставок» (5.2). Где товар
    покупают, а его там нет, — отдельная строка с нулём."""
    out: dict[tuple[str, str], dict] = {}
    for s in stocks:
        own = s["type"] != "FBO"
        name = s["warehouse"] if own or not s["cluster_name"] else s["cluster_name"]
        key = ("purchase" if own else "supply", name)
        c = out.setdefault(key, {"name": name, "type": s["type"], "present": 0, "in_transit": None, "reserved": None,
                                 "warehouses": [], "supply": None})
        c["present"] += max(s["present"] or 0, 0)
        for field in ("in_transit", "reserved"):
            if s[field] is not None:
                c[field] = (c[field] or 0) + s[field]
        if not own:
            c["warehouses"].append(s["warehouse"])
    for i in supply_items:
        key = (i["kind"], i["destination"]["name"])
        c = out.setdefault(key, {"name": i["destination"]["name"], "type": i["destination"]["warehouse_type"] or "FBO",
                                 "present": 0, "in_transit": None, "reserved": None, "warehouses": [], "supply": None})
        c["supply"] = {k: i[k] for k in ("kind", "urgency", "avg_daily", "rate", "days_left", "to_order", "target_stock",
                                         "arrives_late", "sold_30")}
    rank = lambda c: URGENCY_RANK.get(c["supply"]["urgency"], 9) if c["supply"] else 9  # noqa: E731
    return sorted(out.values(), key=lambda c: (rank(c), -c["present"], c["name"]))


def risk(row: dict, supply_items: list[dict]) -> dict:
    """Риск дефицита одной строкой: срочность поставки в кластер (5.2), иначе статус остатка по порогам (5.1)."""
    urgent = sorted(supply_items, key=lambda i: (URGENCY_RANK[i["urgency"]], i["days_left"] if i["days_left"] is not None
                                                 else Decimal(0)))
    if urgent and urgent[0]["urgency"] in ("critical", "urgent"):
        i = urgent[0]
        where = i["destination"]["name"]
        left = f"запаса на {num(i['days_left'])} дн." if i["days_left"] is not None else "товара там нет"
        late = " — кончится раньше, чем доедет поставка" if i["arrives_late"] else ""
        more = len([x for x in urgent if x["urgency"] in ("critical", "urgent")]) - 1
        tail = f"; ещё мест с риском: {more}" if more > 0 else ""
        return {"level": "critical" if i["urgency"] == "critical" else "low",
                "text": f"«{where}»: {left}{late}, отправить {i['to_order']} шт.{tail}"}
    status, days = row["stock_status"], row["days_of_supply"]
    if status in ("critical", "low"):
        return {"level": status, "text": f"Запаса на всех складах — на {num(days)} дн. продаж"}
    if status == "excess":
        return {"level": "excess", "text": f"Запаса — на {num(days)} дн. продаж: больше, чем нужно"}
    if status == "normal":
        return {"level": "ok", "text": f"Запаса на {num(days)} дн. продаж — дефицита не ожидается"}
    if row["stock"] == 0:
        return {"level": "unknown", "text": "На складах товара нет, продаж за 30 дней мало — запас не посчитать"}
    return {"level": "unknown", "text": "Мало данных о продажах — дни запаса не посчитать"}
