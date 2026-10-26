"""«Финансы → Обзор» (шаг 10.22): выручка, расходы, прибыль и рентабельность за период к прошлому такому же периоду и
структура расходов — по P&L с прочими расходами бизнеса (`pnl_statement`), а не новым расчётом.

Прибыль — чистая (после налога и расходов бизнеса), если налог задан; иначе — до налога. Расходы = выручка − прибыль:
всё, что ушло из выручки, — строки P&L (`engine/pnl_statement`): возвраты, удержания Ozon по группам (как «Аналитика
прибыльности»), доплаты Ozon — в минус, себестоимость, расходы бизнеса по статьям, налог и НДС. Сумма групп = расходы до копейки. Деньги на Ozon и на счетах,
ближайшие выплаты, риски и финансовые проблемы страница берёт из своих разделов (выплаты 3.6, «Планирование», 10.5).
"""
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import pnl_service, pnl_statement
from app.services.dashboard import previous_period

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _d(v) -> Decimal | None:
    return None if v is None else Decimal(v)


def _pct(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or whole is None or whole <= 0:
        return None
    return (part / whole * 100).quantize(CENT, ROUND_HALF_UP)


def _change(cur: Decimal | None, prev: Decimal | None) -> Decimal | None:
    if cur is None or prev is None or prev == 0:
        return None
    return ((cur - prev) / abs(prev) * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


def figures(value: dict, kind: str | None = None) -> dict | None:
    """Выручка, прибыль, расходы, рентабельность периода; kind — какую прибыль брать (как в текущем периоде)."""
    st = value.get("statement")
    if not st:
        return None
    revenue = Decimal(st["revenue"])
    kind = kind or ("net" if st["net"] is not None else "before_tax")
    profit = _d(st["net"]) if kind == "net" else Decimal(st["profit_before_tax"])
    if profit is None:
        return None
    return {"kind": kind, "revenue": revenue, "profit": profit, "expenses": revenue - profit,
            "margin": _pct(profit, revenue)}


# строки P&L (engine/pnl_statement), из которых складываются расходы: всё между выручкой и прибылью
OZON_KEYS = ("returns", "adjustments", "commission", "logistics", "storage", "ads", "other", "manual")


def structure(value: dict, kind: str) -> list[dict]:
    """Куда ушла выручка — строки P&L: возвраты, доплаты и удержания Ozon, себестоимость, статьи расходов бизнеса, налог.
    Расход — больше нуля; доплаты Ozon (баллы за скидки, компенсации) — меньше нуля: они уменьшают расходы."""
    st = value["statement"]
    revenue = Decimal(st["revenue"])
    items = []
    for r in st["rows"]:
        if r["key"] in OZON_KEYS or r["key"] == "cost":
            items.append({"key": r["key"], "title": r["title"], "kind": "cost" if r["key"] == "cost" else "ozon",
                          "amount": -Decimal(r["amount"]), "category_id": None})
    for c in (value.get("business") or {}).get("categories", []):
        items.append({"key": f"business:{c['id']}", "title": c["name"], "kind": "business", "amount": Decimal(c["amount"]),
                      "category_id": c["id"]})
    if kind == "net":
        tax = Decimal(st["tax"]) + Decimal(st["vat"] or 0)
        items.append({"key": "tax", "title": f"Налог · {st['tax_title']}" if st["tax_title"] else "Налог", "kind": "tax",
                      "amount": tax, "category_id": None})
    items = [i for i in items if i["amount"]]
    items.sort(key=lambda i: -i["amount"])
    for i in items:
        i["share"] = _pct(i["amount"], revenue)
    return items


async def overview(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    prev_from, prev_to = previous_period(date_from, date_to)
    cur = await pnl_statement.full(db, user_id, pnl_service.PnLRequest(date_from, date_to))
    prev = await pnl_statement.full(db, user_id, pnl_service.PnLRequest(prev_from, prev_to))
    now = figures(cur)
    before = figures(prev, now["kind"]) if now else None
    business = cur["business"]
    st = cur["statement"]

    def fig(key: str) -> dict:
        value = now[key] if now else None
        was = before[key] if before else None
        out = {"value": value, "previous": was}
        if key == "margin":
            out["change_pp"] = None if value is None or was is None else (value - was).quantize(Decimal("0.1"))
        else:
            out["change_pct"] = _change(value, was)
        return out

    return {
        "date_from": date_from, "date_to": date_to,
        "previous": {"date_from": prev_from, "date_to": prev_to, "has_data": before is not None},
        "data_source": cur["data_source"], "coverage": cur["coverage"], "has_data": now is not None,
        "profit_kind": now["kind"] if now else None, "tax_set": bool(st and st["net"] is not None),
        "figures": {k: fig(k) for k in ("revenue", "expenses", "profit", "margin")},
        "profit_ozon": _d(st["profit_ozon"]) if st else None,
        "business": None if business is None else {
            "total": Decimal(business["total"]), "operations": business["operations"],
            "uncategorized": business["uncategorized"],
        },
        "structure": structure(cur, now["kind"]) if now else [],
        "missing_cost": len(cur["missing_cost"]),
        "warnings": cur["warnings"],
    }
