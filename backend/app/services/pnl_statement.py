"""P&L периода как отчёт о прибылях и убытках с прочими расходами бизнеса (шаг 10.22).

P&L Ozon (3.2) берётся как есть — из кэша и снимков закрытых месяцев; расходы бизнеса — из «Операций» каждый раз заново:
операции меняются отдельно от начислений, и их не надо ждать пять минут кэша. Налог с расходами бизнеса пересчитывается
по тем же суммам, что у P&L (`tax_input`), и тому же профилю: у режимов «от прибыли» расходы уменьшают базу, у «от
дохода» и патента — нет. Без расходов бизнеса цифры — ровно P&L 3.1.
"""
import uuid
from dataclasses import replace
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import pnl_statement as eng
from app.engine.tax import compute_tax
from app.models.user_settings import COST_LABELS
from app.services import pnl_business, pnl_service
from app.services.tax_profile import load_profiles, profile_on
from app.services.user_settings import get_settings

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(v)


def _money(v: Decimal | None) -> str | None:
    return None if v is None else str(Decimal(v).quantize(CENT))


def _row_out(r: eng.Row, revenue: Decimal) -> dict:
    return {
        "key": r.key, "title": r.title, "amount": _money(r.amount), "kind": r.kind, "pct": _money(r.pct(revenue)),
        "types": [{"name": n, "amount": _money(a)} for n, a in r.types],
        "categories": [pnl_business.category_out(c) for c in r.children],
    }


async def _tax(db: AsyncSession, user_id: uuid.UUID, req: pnl_service.PnLRequest, value: dict, business: Decimal):
    """Налог и НДС с расходами бизнеса по тому же профилю, что у P&L; налог не задан — (None, None, [])."""
    row = profile_on(await load_profiles(db, user_id), req.date_from)
    profile, _ = pnl_service.tax_profile(req, row)
    if profile is None:
        return None, None, []
    data = replace(pnl_service.tax_input_in(value["tax_input"]), business_expenses=business)
    result = compute_tax(profile, data, req.date_from)
    return result.tax, result.vat, result.lines


async def extend(db: AsyncSession, user_id: uuid.UUID, req: pnl_service.PnLRequest, value: dict) -> dict:
    """Ответ P&L + `business` (расходы бизнеса за период или None) и `statement` (строки отчёта; нет данных — None)."""
    business = await pnl_business.expenses(db, user_id, req.date_from, req.date_to)
    statement = None
    p = value.get("pnl")
    if p:
        if not value.get("tax_input"):  # ответ из кэша до шага 10.22 — считаем заново
            value = await pnl_service.compute(db, user_id, req)
            p = value["pnl"]
        spent = business["total"] if business else ZERO
        tax, vat, lines = _dec(p["tax"]), _dec(p["vat"]), p["tax_lines"]
        if spent:
            tax, vat, lines = await _tax(db, user_id, req, value, spent)
        settings = await get_settings(db, user_id)
        cost_title = COST_LABELS[settings.supplier_payment]  # как в P&L: «Себестоимость с НДС», «Долг поставщику» …
        s = eng.build(
            {k: Decimal(v) for k, v in value["by_type"].items()}, Decimal(value["revenue_base"]),
            Decimal(value["auto_total"]), Decimal(p["effective_total"]), Decimal(p["owed"]), cost_title,
            business["categories"] if business else None, tax, vat, p["tax_title"], net_ozon=_dec(p["net"]),
        )
        statement = {
            "rows": [_row_out(r, s.revenue) for r in s.rows],
            "revenue": _money(s.revenue), "profit_ozon": _money(s.profit_ozon), "business": _money(s.business),
            "profit_before_tax": _money(s.profit), "tax": _money(s.tax), "vat": _money(s.vat), "net": _money(s.net),
            "margin": _money(s.margin), "tax_title": p["tax_title"], "tax_lines": lines,
        }
    out_business = None
    if business is not None:
        out_business = {
            "total": _money(business["total"]), "operations": business["operations"],
            "categories": [pnl_business.category_out(c) for c in business["categories"]],
            "excluded": [{**x, "amount": _money(x["amount"])} for x in business["excluded"]],
            "uncategorized": {"count": business["uncategorized"]["count"],
                              "amount": _money(business["uncategorized"]["amount"])},
        }
    return {**value, "business": out_business, "statement": statement}


async def full(db: AsyncSession, user_id: uuid.UUID, req: pnl_service.PnLRequest) -> dict:
    """P&L периода с расходами бизнеса — для «Обзора финансов»."""
    return await extend(db, user_id, req, await pnl_service.get_pnl(db, user_id, req))
