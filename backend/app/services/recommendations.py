"""Рекомендуемые действия (шаг 10.6): данные для правил engine/recommendations — из готовых расчётов, скрытие на N дней.

Откуда: продажи с данными за 30 дней — покрытие начислений (3.2); цена, минимальная цена, маржа и самая низкая цена в
акциях — таблица цен (калькулятор 4.2); продажи в день — 3.5; поставки «Срочно / Скоро» — «Поставки» (5.2) с
сохранёнными параметрами; без продаж и к вывозу — «На вывоз» (5.1); продажи без себестоимости — справочник 2.3.

«Скрыть» — запись на N дней (настройка магазина, по умолчанию 7); «Вернуть скрытые» — cancelled_at. «Сделано» не
отмечается: условие ушло — рекомендация исчезает сама.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import recommendations as engine
from app.engine import supply_planner
from app.engine import withdrawal as withdrawal_engine
from app.models.recommendation import RecommendationHide
from app.schemas.sync import utc
from app.services import pnl_data, pricing, sales, supply, withdrawal
from app.services.costs import sales_without_cost
from app.services.stocks import avg_daily_sales
from app.services.sync.finance import MSK, today_msk
from app.services.user_settings import get_settings, recommendation_hide_days


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day(dt: datetime | None) -> date | None:
    return None if dt is None else utc(dt).astimezone(MSK).date()


async def gather(db: AsyncSession, user_id: uuid.UUID) -> engine.Inputs:
    today = today_msk()
    end = today - timedelta(days=1)
    start = today - timedelta(days=engine.WINDOW_DAYS)
    cov = await pnl_data.coverage(db, user_id, start, end, "auto")
    covered = {d for d, source in cov.days.items() if source is not None}
    await sales.ensure_built(db, user_id)

    rows, pctx = await pricing.table(db, user_id)  # товары не в архиве
    avg = await avg_daily_sales(db, user_id)
    products: dict[uuid.UUID, engine.ProductData] = {}
    vat_cost: dict[uuid.UUID, Decimal] = {}
    for r in rows:
        record = pctx.history.record_at(r["offer_id"], today)
        if record is not None:
            vat_cost[r["id"]] = Decimal(record.cost_price) * (record.vat_multiplier or pctx.default_vat)
        promo = r["promo"]
        products[r["id"]] = engine.ProductData(
            engine.ProductRef(r["id"], r["offer_id"], r["name"]), r["price"], record is not None, avg.get(r["id"]),
            r["min_price"], r["target_price"], r["margin"], r["unit_profit"],
            engine.PromoData(promo["id"], promo["title"], promo["action_price"], promo["margin"], _day(promo["date_to"]),
                             promo["added_automatically"]) if promo else None,
        )

    params = await supply.load_params(db, user_id)
    plan = await supply.report(db, user_id, params)
    # отправлений с данными за 30 дней: поставки — FBO, закупки на свой склад — FBS
    days = {supply_planner.SUPPLY: (plan["fbo"] or {}).get("days", 0),
            supply_planner.PURCHASE: (plan["fbs"] or {}).get("days", 0)}
    lines = [
        engine.SupplyLine(i["product"]["id"], i["kind"], i["urgency"], i["destination"]["name"], i["to_order"],
                          i["days_left"], i["avg_daily"], enough=days[i["kind"]] >= engine.MIN_DATA_DAYS)
        for i in plan["items"]
    ]

    wparams = await withdrawal.load_params(db, user_id)
    out = await withdrawal.report(db, user_id, wparams)
    idle = [
        engine.IdleStock(i["product"]["id"], i["available"], i["qty"], i["days_without_sales"],
                         vat_cost.get(i["product"]["id"]))
        for i in out["items"] if i["kind"] == withdrawal_engine.IDLE
    ]
    return engine.Inputs(
        today=today, data_days=len(covered), products=products, supply=lines, idle=idle,
        cost_gaps=await sales_without_cost(db, user_id, start, end, covered, pctx.history),
        target_days=params.target_days, lead_time_days=params.lead_time_days, keep_idle=wparams.keep_idle,
    )


async def active_hides(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> dict[str, datetime]:
    """Скрытые сейчас: ключ → до какого времени."""
    out: dict[str, datetime] = {}
    for key, until in await db.execute(select(RecommendationHide.key, RecommendationHide.hidden_until).where(
        RecommendationHide.user_id == user_id, RecommendationHide.cancelled_at.is_(None),
        RecommendationHide.hidden_until > now,
    )):
        until = utc(until)
        out[key] = max(out.get(key, until), until)
    return out


def item(r: engine.Recommendation) -> dict:
    return {
        "kind": r.kind, "key": r.key, "title": r.title, "why": r.why, "basis": r.basis, "action": r.action, "link": r.link,
        "product": {"id": r.product.id, "offer_id": r.product.offer_id, "name": r.product.name} if r.product else None,
        "money": r.money, "money_label": r.money_label,
    }


async def build(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID | None = None,
                now: datetime | None = None) -> dict:
    """Рекомендации магазина (или товара) без скрытых — по порядку правил."""
    now = now or _now()
    inp = await gather(db, user_id)
    recs = engine.recommend(inp, product_id)
    hidden = await active_hides(db, user_id, now)
    shown = [r for r in recs if r.key not in hidden]
    return {
        "items": [item(r) for r in shown], "total": len(shown), "hidden": len(recs) - len(shown),
        "data_days": inp.data_days, "min_days": engine.MIN_DATA_DAYS, "enough_data": engine.enough_data(inp),
        "hide_days": recommendation_hide_days(await get_settings(db, user_id)),
    }


async def hide(db: AsyncSession, user_id: uuid.UUID, key: str, now: datetime | None = None) -> datetime:
    """«Скрыть» на N дней — новая запись."""
    now = now or _now()
    until = now + timedelta(days=recommendation_hide_days(await get_settings(db, user_id)))
    db.add(RecommendationHide(user_id=user_id, key=key, hidden_until=until, created_at=now))
    await db.commit()
    return until


async def unhide_all(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> int:
    """«Вернуть скрытые» — все действующие скрытия отменяются (записи остаются)."""
    now = now or _now()
    rows = list((await db.execute(select(RecommendationHide).where(
        RecommendationHide.user_id == user_id, RecommendationHide.cancelled_at.is_(None),
        RecommendationHide.hidden_until > now,
    ))).scalars())
    for r in rows:
        r.cancelled_at = now
    await db.commit()
    return len(rows)
