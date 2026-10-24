"""Результаты акций (шаг 10.16): продажи, выручка и прибыль товара за время участия против такого же периода до входа
(`engine/promo_results`). Значения по дням — тем же движком, что «Прибыль по товарам» и аналитика по товару
(`product_pnl.runner` над строками начислений дня, как `services/analytics`), — цифры совпадают с ними.
Дни без начислений — пропуск (`pnl_data.coverage`). Участие — строка `promotion_products` (4.3): вход — `first_seen_at`,
выход — `left_at`, у идущего — конец акции или сегодня.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import promo_results as engine
from app.models.product import Product
from app.models.promotion import Promotion, PromotionProduct
from app.services import pnl_data, product_pnl
from app.services.pnl_service import PnLRequest
from app.services.sync.finance import MSK, today_msk


def _day(at: datetime | None) -> date | None:
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(MSK).date()


def participation_window(r: PromotionProduct, promo: Promotion, today: date) -> tuple[date, date]:
    """Дни участия: с входа по выход, конец акции или сегодня (что раньше)."""
    entered = _day(r.first_seen_at) or today
    end = _day(r.left_at) or today
    if promo.date_to is not None:
        end = min(end, _day(promo.date_to))
    return entered, min(end, today)


def totals_out(t: engine.Totals) -> dict:
    return {"date_from": t.date_from, "date_to": t.date_to, "days": t.days, "days_with_data": t.days_with_data,
            "qty": t.qty, "revenue": t.revenue, "profit": t.profit}


async def results(
    db: AsyncSession, user_id: uuid.UUID, rows: list[tuple[PromotionProduct, Promotion, Product]]
) -> dict[uuid.UUID, dict]:
    """Результат каждого участия: окна «во время» и «до», итоги, изменение. Ключ — id строки участия."""
    if not rows:
        return {}
    today = today_msk()
    plans = {}
    for r, promo, product in rows:
        during, before = engine.windows(*participation_window(r, promo, today))
        plans[r.id] = (product.offer_id, during, before)
    start = min(b[0] for _, _, b in plans.values())
    end = max(d[1] for _, d, _ in plans.values())
    offers = {offer for offer, _, _ in plans.values()}
    cov = await pnl_data.coverage(db, user_id, start, end, "auto")
    # строки других товаров на эти товары не влияют; без артикула — оставляем (относятся по номеру отправления)
    lines = [ln for ln in await pnl_data.load_lines(db, user_id, cov) if not ln.offer_id or ln.offer_id in offers]
    run = await product_pnl.runner(db, user_id, PnLRequest(start, end), lines)
    by_day: dict[date, list] = defaultdict(list)
    for ln in lines:
        by_day[ln.day].append(ln)
    values: dict[str, dict[date, engine.Day | None]] = {offer: {} for offer in offers}
    for day, source in cov.days.items():
        if source is None:
            continue  # нет начислений — пропуск, а не 0
        found = {row.offer_id: row for row in run(by_day.get(day, [])).rows if row.offer_id in offers}
        for offer in offers:
            row = found.get(offer)
            values[offer][day] = engine.Day() if row is None else engine.Day(row.qty, row.revenue, row.profit)
    out = {}
    for rid, (offer, during, before) in plans.items():
        d = engine.totals(values[offer], *during)
        b = engine.totals(values[offer], *before)
        change = engine.compare(d, b)
        out[rid] = {
            "during": totals_out(d), "before": totals_out(b),
            "base": totals_out(change["base"]) if change["base"] is not None else None,
            "known": change["known"], "qty_change_pct": change["qty_change_pct"],
            "revenue_change_pct": change["revenue_change_pct"], "profit_change": change["profit_change"],
            "_pair": (d, b),
        }
    return out


def overall(items: dict[uuid.UUID, dict]) -> dict:
    """По акции в целом — сумма товаров с данными в обоих окнах."""
    return engine.combine([r["_pair"] for r in items.values()])


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}
