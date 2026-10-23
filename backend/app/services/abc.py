"""ABC-анализ товаров (шаг 10.10): группы по выручке, прибыли или продажам в штуках за период, прошлый период и смена
группы, сравнение ABC по выручке и по прибыли (матрица 3 × 3), выгрузка в Excel.

Показатели товаров — «Прибыль по товарам» (3.4) за период: выручка, прибыль до налога (без себестоимости — неизвестна,
правило 5), продано, шт. — те же цифры, что в «Аналитике прибыльности» и «Обзоре бизнеса». Группы — `engine/abc`,
границы — настройка пользователя (80 / 95 % по умолчанию). Фильтр товаров и категории — как у показателей: ABC среди
выбранных. Кэш — вместе с P&L.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import abc
from app.models.product import Product
from app.services import pnl_service, product_pnl
from app.services.analytics import AnalyticsError, _out, _products, _ref
from app.services.dashboard import previous_period
from app.services.pnl_cache import cache
from app.services.user_settings import abc_bounds, get_settings
from app.services.xlsx_export import Col, table_xlsx

CRITERIA = {"revenue": "Выручка", "profit": "Прибыль до налога", "qty": "Продано, шт."}


def _value(row: dict, criterion: str) -> Decimal | None:
    if criterion == "qty":
        return Decimal(row["qty"])
    v = row[criterion]
    return None if v is None else Decimal(v)


async def _rows(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date,
                offers: set[str] | None) -> tuple[dict[str, dict], str]:
    """Товары периода из «Прибыли по товарам» (только фильтра) и источник данных."""
    value = await product_pnl.compute(db, store_id, pnl_service.PnLRequest(date_from, date_to))
    rows = {r["offer_id"]: r for r in value["items"] if offers is None or r["offer_id"] in offers}
    return rows, value["data_source"]


def _groups(rows: dict[str, dict], criterion: str, bounds: abc.Bounds) -> dict[str, abc.Row]:
    return {r.key: r for r in abc.classify({k: _value(v, criterion) for k, v in rows.items()}, bounds)}


async def analysis(db: AsyncSession, store_id: uuid.UUID, date_from: date, date_to: date, criterion: str = "revenue",
                   product_ids: list[uuid.UUID] | None = None, category_id: int | None = None) -> dict:
    """Группы A, B, C по критерию за период: товары (доля, накопленная доля, прошлый период и группа тогда), сводка по
    группам, убыточные, матрица «выручка × прибыль»."""
    if criterion not in CRITERIA:
        raise AnalyticsError("Критерий ABC: выручка (revenue), прибыль (profit) или продажи в штуках (qty)")
    try:
        pnl_service.PnLRequest(date_from, date_to).check()
    except pnl_service.PnLRequestError as exc:
        raise AnalyticsError(str(exc)) from None
    bounds = abc_bounds(await get_settings(db, store_id))
    product_ids = sorted(set(product_ids or []), key=str)
    params = {"from": date_from.isoformat(), "to": date_to.isoformat(), "products": [str(p) for p in product_ids],
              "category": category_id, "by": criterion, "bounds": [str(bounds.a), str(bounds.b)]}
    if (hit := await cache.get(store_id, "abc", params)) is not None:
        return hit

    products = await _products(db, store_id, product_ids, category_id)
    offers = None if products is None else {p.offer_id for p in products}
    catalog = {p.offer_id: p for p in (await db.execute(select(Product).where(Product.user_id == store_id))).scalars()}
    rows, source = await _rows(db, store_id, date_from, date_to, offers)
    prev_from, prev_to = previous_period(date_from, date_to)
    prev_rows, prev_source = await _rows(db, store_id, prev_from, prev_to, offers)

    current = _groups(rows, criterion, bounds)
    prev = _groups(prev_rows, criterion, bounds)
    by_revenue = _groups(rows, "revenue", bounds)
    by_profit = _groups(rows, "profit", bounds)

    def item(r: abc.Row) -> dict:
        p = prev.get(r.key)
        return {
            "product": _ref(r.key, catalog), "group": r.group, "value": r.value, "rank": r.rank, "share": r.share,
            "cum_share": r.cum_share, "loss": r.loss, "prev_value": p.value if p else None,
            "prev_group": p.group if p else None, "change_pct": abc.change_pct(r.value, p.value if p else None),
            "revenue_group": by_revenue[r.key].group, "profit_group": by_profit[r.key].group,
            "profit_loss": by_profit[r.key].loss,
        }

    ordered = list(current.values())
    groups = abc.summary(ordered)
    cells = abc.matrix({k: r.group for k, r in by_revenue.items()}, {k: r.group for k, r in by_profit.items()})
    out = _out({
        "date_from": date_from, "date_to": date_to, "criterion": criterion,
        "bounds": {"a": int(bounds.a), "b": int(bounds.b)}, "data_source": source,
        "previous": {"date_from": prev_from, "date_to": prev_to, "has_data": prev_source != "none"},
        "total": sum((r.value for r in ordered if r.value is not None and r.value > 0), Decimal(0)),
        "groups": [{"group": g.group, "count": g.count, "value": g.value, "share": g.share,
                    "product_share": g.product_share} for g in groups],
        "loss": {"count": sum(1 for r in ordered if r.loss),
                 "value": sum((r.value for r in ordered if r.loss), Decimal(0))},
        "unknown": sum(1 for r in ordered if r.group is None),
        "items": [item(r) for r in ordered],
        "matrix": [{"revenue_group": rg, "profit_group": pg, "count": len(keys)} for (rg, pg), keys in sorted(
            cells.items(), key=lambda kv: (kv[0][0], kv[0][1] or "Z"))],
    })
    await cache.set(store_id, "abc", params, out)
    return out


COLUMNS = [
    Col("Артикул", 18), Col("Название", 36), Col("Группа", 9), Col("Показатель", 16, "number"),
    Col("Доля, %", 10, "number"), Col("Накопленная доля, %", 14, "number"), Col("Прошлый период", 16, "number"),
    Col("Группа тогда", 10), Col("Изменение, %", 12, "number"), Col("ABC по выручке", 11), Col("ABC по прибыли", 11),
    Col("Убыточный", 11),
]


def to_xlsx(data: dict, filter_text: str) -> bytes:
    def num(v):
        return None if v is None else Decimal(v)

    rows = [[i["product"]["offer_id"], i["product"]["name"] or "", i["group"] or "нет данных", num(i["value"]),
             num(i["share"]), num(i["cum_share"]), num(i["prev_value"]), i["prev_group"] or "", num(i["change_pct"]),
             i["revenue_group"] or "", i["profit_group"] or "нет себестоимости", "да" if i["profit_loss"] else ""]
            for i in data["items"]]
    fmt = lambda iso: date.fromisoformat(iso).strftime("%d.%m.%Y")  # noqa: E731
    b = data["bounds"]
    groups = "; ".join(f"{g['group']} — {g['count']} тов., {Decimal(g['share']):.1f} %" for g in data["groups"])
    return table_xlsx(
        "ABC-анализ", COLUMNS, rows,
        notes=[
            f"ABC-анализ: {CRITERIA[data['criterion']].lower()}",
            f"Период: {fmt(data['date_from'])} – {fmt(data['date_to'])}; прошлый период: "
            f"{fmt(data['previous']['date_from'])} – {fmt(data['previous']['date_to'])}.",
            f"Товары: {filter_text}.",
            f"Группы: A — пока накопленная доля до товара меньше {b['a']} %, B — меньше {b['b']} %, C — остальные; "
            "товар, на котором доля переходит границу, — в старшей группе; равные значения — в одной группе. Границы — "
            "в «Настройках».",
            f"Итого: {groups}.",
            "Ноль и убыток — группа C; убыточные помечены, их доля не считается. Прибыль — до налога, как в «Прибыли по "
            "товарам»; без себестоимости прибыль неизвестна — группы по прибыли нет.",
        ],
    )
