"""Отчёт «Кандидаты на вывоз» (шаг 5.1): что вывезти со складов Ozon, из какого кластера и склада, сколько.

Расчёт — engine/withdrawal (BUSINESS_LOGIC §6). Остатки, продажи, дни запаса и «сколько не продавался» —
services/stocks (по товару в целом, пороги пользователя). Параметры отчёта сохраняются у пользователя
(`user_settings.withdrawal_*`); в запросе их можно переопределить — посмотреть, что получится, не сохраняя.
"""
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import withdrawal as engine
from app.models.user_settings import UserSettings
from app.models.warehouse import FBO
from app.services.stocks import StocksView, load_view
from app.services.user_settings import defaults, get_settings
from app.services.xlsx_export import Col, table_xlsx

MAX_TOTAL = 1_000_000


def saved_params(s: UserSettings) -> engine.Params:
    """Сохранённые параметры; не заданы — по умолчанию: 2 и 10 шт., без лимита, ни один кластер не исключён."""
    return engine.Params(
        total=s.withdrawal_total or None,
        keep_idle=engine.DEFAULT_KEEP_IDLE if s.withdrawal_keep_cluster is None else s.withdrawal_keep_cluster,
        keep_selling=engine.DEFAULT_KEEP_SELLING if s.withdrawal_keep_selling is None else s.withdrawal_keep_selling,
        excluded=frozenset(str(k) for k in s.withdrawal_excluded_clusters or []),
    )


async def load_params(db: AsyncSession, user_id: uuid.UUID) -> engine.Params:
    return saved_params(await get_settings(db, user_id))


def with_overrides(
    base: engine.Params,
    *,
    total: int | None = None,
    keep_idle: int | None = None,
    keep_selling: int | None = None,
    excluded: list[str] | None = None,
) -> engine.Params:
    """Переданное в запросе — вместо сохранённого. total = 0 — без ограничения; excluded = [] — ни один кластер."""
    changes: dict = {}
    if total is not None:
        changes["total"] = total or None
    if keep_idle is not None:
        changes["keep_idle"] = keep_idle
    if keep_selling is not None:
        changes["keep_selling"] = keep_selling
    if excluded is not None:
        changes["excluded"] = frozenset(excluded)
    return replace(base, **changes)


async def save_params(db: AsyncSession, user_id: uuid.UUID, params: engine.Params) -> engine.Params:
    s = await db.get(UserSettings, user_id)
    if s is None:
        s = defaults(user_id)
        db.add(s)
    s.withdrawal_total = params.total
    s.withdrawal_keep_cluster = params.keep_idle
    s.withdrawal_keep_selling = params.keep_selling
    s.withdrawal_excluded_clusters = sorted(params.excluded)
    await db.commit()
    return saved_params(s)


def params_out(p: engine.Params) -> dict:
    return {"total": p.total, "keep_idle": p.keep_idle, "keep_selling": p.keep_selling, "excluded": sorted(p.excluded)}


def items(view: StocksView) -> list[engine.Item]:
    out = []
    for info in view.products.values():
        places = [
            engine.Place(
                warehouse_id=w.id, warehouse=w.name, cluster_key=str(w.cluster_id) if w.cluster_id else None,
                cluster=w.cluster_name, present=s.present,
            )
            for s, w in info.places if w.type == FBO and s.present > 0
        ]
        out.append(engine.Item(
            key=info.product.id, offer_id=info.product.offer_id, available=info.available,
            avg_daily=info.avg_daily_sales, days_without_sales=info.days_without_sales, places=places,
        ))
    return out


def clusters(view: StocksView, excluded: frozenset[str] = frozenset()) -> list[dict]:
    """Кластеры Ozon, где лежат товары (и исключённые, даже если там пусто), — для выбора исключённых: сколько штук и
    товаров в каждом."""
    found: dict[str, dict] = {}
    for info in view.products.values():
        for s, w in info.places:
            if w.type != FBO or not w.cluster_id:
                continue
            c = found.setdefault(str(w.cluster_id), {"key": str(w.cluster_id), "name": w.cluster_name or str(w.cluster_id),
                                                     "present": 0, "products": set()})
            c["present"] += max(s.present, 0)
            if s.present > 0:
                c["products"].add(info.product.id)
    return sorted(
        ({**c, "products": len(c["products"])} for c in found.values() if c["present"] > 0 or c["key"] in excluded),
        key=lambda c: (-c["present"], c["name"]),
    )


def reason(c: engine.Candidate, sales_since: date | None) -> str:
    """Почему на вывоз — для таблицы и Excel."""
    if c.kind == engine.IDLE:
        dws = c.item.days_without_sales
        if dws is None:
            return f"Нет продаж с {sales_since:%d.%m.%Y}" if sales_since else "Продаж не было"
        return f"Нет продаж {dws} дн."
    avg = f"{Decimal(c.item.avg_daily or 0).quantize(Decimal('0.01')).normalize():f}".replace(".", ",")
    return f"Запас на {int(c.days_of_supply or 0)} дн. при продажах {avg} шт./день"


async def report(db: AsyncSession, user_id: uuid.UUID, params: engine.Params) -> dict:
    view = await load_view(db, user_id)
    saved = await load_params(db, user_id)
    result = engine.plan(items(view), params, view.thresholds)
    out_items = []
    for c in result.candidates:
        info = view.products[c.item.key]
        p = info.product
        out_items.append({
            "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku},
            "kind": c.kind, "reason": reason(c, view.sales_since), "keep": c.keep, "surplus": c.surplus,
            "available": info.available, "avg_daily_sales": info.avg_daily_sales, "days_of_supply": c.days_of_supply,
            "last_sale": info.last_sale, "days_without_sales": info.days_without_sales,
            "possible": c.possible, "qty": c.qty, "excluded_units": c.excluded_units,
            "lines": [
                {"warehouse_id": line.place.warehouse_id, "warehouse": line.place.warehouse,
                 "cluster_key": line.place.cluster_key, "cluster": line.place.cluster, "present": line.place.present,
                 "qty": line.qty, "cluster_left": line.cluster_left}
                for line in c.lines
            ],
        })
    t = view.thresholds
    return {
        "params": params_out(params), "saved": params_out(saved), "is_saved": params == saved,
        "thresholds": {"critical": t.critical, "low": t.low, "excess": t.excess},
        "sales_known": view.sales_known, "stocks_known": view.synced_at is not None, "sales_since": view.sales_since,
        "today": view.today, "clusters": clusters(view, params.excluded | saved.excluded),
        "possible": result.possible, "planned": result.planned, "left_out": result.left_out,
        "items": out_items,
    }


# ── Excel для логиста ──
COLUMNS = [
    Col("Артикул", 26), Col("Название", 40), Col("Кластер", 28), Col("Склад", 26),
    Col("Сколько вывезти, шт.", 14, "int"), Col("На складе, шт.", 12, "int"), Col("Останется в кластере, шт.", 14, "int"),
    Col("Причина", 40), Col("Последняя продажа", 14, "date"),
]


def _excluded_names(data: dict) -> list[str]:
    names = {c["key"]: c["name"] for c in data["clusters"]}
    return [names.get(k, f"кластер {k}") for k in data["params"]["excluded"]]


def to_xlsx(data: dict) -> bytes:
    rows = []
    for it in data["items"]:
        p = it["product"]
        for line in it["lines"]:
            rows.append([
                p["offer_id"], p["name"], line["cluster"] or "—", line["warehouse"], line["qty"], line["present"],
                line["cluster_left"], it["reason"], it["last_sale"],
            ])
    prm = data["params"]
    excluded = _excluded_names(data)
    notes = [
        f"Кандидаты на вывоз со складов Ozon — {data['today']:%d.%m.%Y}",
        "",
        f"Вывезти всего: {prm['total']} шт." if prm["total"] else "Вывезти всего: без ограничения",
        f"Оставлять в каждом кластере: {prm['keep_idle']} шт. у товара без продаж, {prm['keep_selling']} шт. у продающегося",
        "Не трогать кластеры: " + (", ".join(excluded) if excluded else "нет"),
        f"Пороги остатка: «Избыток» — запаса больше {data['thresholds']['excess']} дн.",
        "",
        "Кто кандидат: товар, которого лежит больше, чем продастся за порог «Избыток», или который не продаётся. "
        "Сначала — дольше не продававшиеся.",
        "Продающийся товар вывозится только сверх запаса на порог «Избыток» — после вывоза его хватит на это время.",
        "Продажи — по товару в целом (по всем складам), за последние 30 дней.",
        f"Итого: {data['planned']} шт. из {data['possible']} возможных.",
    ]
    total = ["Итого", None, None, None, data["planned"], None, None, None, None]
    return table_xlsx("Вывоз", COLUMNS, rows, total=total, notes_title="Параметры", notes=notes)
