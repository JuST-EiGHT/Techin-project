"""Отчёт «Кандидаты на вывоз» (шаг 5.1): что вывезти со складов Ozon, из какого кластера и склада, сколько.

Расчёт — engine/withdrawal (BUSINESS_LOGIC §6). Остатки, продажи, дни запаса и «сколько не продавался» —
services/stocks (по товару в целом, пороги пользователя). Параметры отчёта сохраняются у пользователя
(`user_settings.withdrawal_*`); в запросе их можно переопределить — посмотреть, что получится, не сохраняя.

10.17: у строки — причина и приоритет (engine), продано за 30 и 90 дней, замороженные деньги (вывезти × себестоимость с
НДС, как стоимость запаса в «Остатках»), решение пользователя «вывозим» / «оставить» с датой (`withdrawal_decisions`,
действует последнее). Фильтры склада, приоритета и решения — только показ (план считается по всем товарам: лимит
«вывезти всего» не зависит от фильтра). Заявку на вывоз Seller API не создаёт — продавец оформляет её в кабинете Ozon;
список уже оформленных вывозов — `/v1/removal/from-stock/list` (бета, только чтение).
"""
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import withdrawal as engine
from app.integrations.ozon.errors import OzonAPIError, OzonAuthError, OzonNotConfiguredError
from app.integrations.ozon.factory import get_ozon_client
from app.models.product import Product
from app.models.user_settings import UserSettings
from app.models.warehouse import FBO
from app.models.withdrawal import KEEP, REMOVE, UNDECIDED, WithdrawalDecision
from app.schemas.sync import utc
from app.services.sales import sold_by_product
from app.services.stock_table import unit_costs
from app.services.stocks import StocksView, load_view
from app.services.sync.finance import today_msk
from app.services.user_settings import defaults, get_settings
from app.services.xlsx_export import Col, table_xlsx

MAX_TOTAL = 1_000_000
OZON_CABINET = "https://seller.ozon.ru/"
CABINET_PATH = "FBO → Вывоз и утилизация"
REASON_TITLE = {engine.LOW_TURNOVER: "Низкая оборачиваемость", engine.EXCESS_STOCK: "Избыточный запас"}
PRIORITY_TEXT = {engine.HIGH: "высокий", engine.MEDIUM: "средний", engine.NORMAL: "обычный"}
DECISION_TEXT = {REMOVE: "вывозим", KEEP: "оставить"}


class WithdrawalError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message, self.status_code = message, status_code
        super().__init__(message)


@dataclass(frozen=True)
class View:
    """Что показать: склад, приоритет, решение (undecided — без решения). План — по всем товарам."""

    warehouse_id: uuid.UUID | None = None
    priority: str | None = None
    decision: str | None = None


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


async def latest_decisions(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, WithdrawalDecision]:
    """Последнее решение по каждому товару; «Отменить решение» (undecided) — решения нет."""
    rows = (await db.execute(
        select(WithdrawalDecision).where(WithdrawalDecision.user_id == user_id)
        .order_by(WithdrawalDecision.created_at, WithdrawalDecision.id)
    )).scalars()
    out: dict[uuid.UUID, WithdrawalDecision] = {}
    for d in rows:
        out[d.product_id] = d
    return {pid: d for pid, d in out.items() if d.decision != UNDECIDED}


async def decide(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID, decision: str,
                 qty: int | None) -> WithdrawalDecision:
    """Новая запись решения; товар — только свой."""
    product = await db.get(Product, product_id)
    if product is None or product.user_id != user_id:
        raise WithdrawalError("Товар не найден", 404)
    d = WithdrawalDecision(user_id=user_id, product_id=product_id, decision=decision, qty=qty,
                           created_at=datetime.now(timezone.utc))  # с микросекундами: порядок решений за одну секунду
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


def decision_out(d: WithdrawalDecision | None) -> dict | None:
    return None if d is None else {"decision": d.decision, "qty": d.qty, "at": utc(d.created_at)}


def _line_out(line: engine.Line) -> dict:
    return {"warehouse_id": line.place.warehouse_id, "warehouse": line.place.warehouse,
            "cluster_key": line.place.cluster_key, "cluster": line.place.cluster, "present": line.place.present,
            "qty": line.qty, "cluster_left": line.cluster_left}


def _warehouses(candidates: list[engine.Candidate]) -> list[dict]:
    """Склады, откуда есть что вывезти, — для фильтра: сколько штук и товаров."""
    found: dict = {}
    for c in candidates:
        for line in c.lines:
            w = found.setdefault(line.place.warehouse_id, {"id": line.place.warehouse_id, "name": line.place.warehouse,
                                                           "cluster": line.place.cluster, "qty": 0, "products": set()})
            w["qty"] += line.qty
            w["products"].add(c.item.key)
    return sorted(({**w, "products": len(w["products"])} for w in found.values()), key=lambda w: (-w["qty"], w["name"]))


async def report(db: AsyncSession, user_id: uuid.UUID, params: engine.Params, view: View = View()) -> dict:
    stocks = await load_view(db, user_id)
    saved = await load_params(db, user_id)
    result = engine.plan(items(stocks), params, stocks.thresholds)
    costs = await unit_costs(db, user_id, stocks.today)
    sold_30 = await sold_by_product(db, user_id, 30, stocks.today)
    sold_90 = await sold_by_product(db, user_id, 90, stocks.today)
    decided = await latest_decisions(db, user_id)
    all_items, frozen_total = [], Decimal(0)
    counts = {"priority": {k: 0 for k in PRIORITY_TEXT}, "decision": {UNDECIDED: 0, REMOVE: 0, KEEP: 0}}
    for c in result.candidates:
        info = stocks.products[c.item.key]
        p = info.product
        kind = engine.reason_kind(c, stocks.thresholds)
        priority = engine.PRIORITY[kind]
        unit_cost = costs.get(p.offer_id)
        d = decided.get(p.id)
        frozen = engine.frozen_money(c.qty, unit_cost)
        frozen_total += frozen or 0
        counts["priority"][priority] += 1
        counts["decision"][d.decision if d else UNDECIDED] += 1
        all_items.append({
            "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku},
            "kind": c.kind, "reason": reason(c, stocks.sales_since), "reason_kind": kind,
            "reason_title": REASON_TITLE.get(kind) or reason(c, stocks.sales_since), "priority": priority,
            "keep": c.keep, "surplus": c.surplus,
            "available": info.available, "avg_daily_sales": info.avg_daily_sales, "days_of_supply": c.days_of_supply,
            "last_sale": info.last_sale, "days_without_sales": info.days_without_sales,
            "sold_30": sold_30.get(p.id), "sold_90": sold_90.get(p.id),
            "possible": c.possible, "qty": c.qty, "qty_all": c.qty, "excluded_units": c.excluded_units,
            "unit_cost": unit_cost, "frozen": frozen, "decision": decision_out(d),
            "lines": [_line_out(line) for line in c.lines],
        })
    shown = []
    for it in all_items:
        if view.priority and it["priority"] != view.priority:
            continue
        if view.decision and (it["decision"] or {}).get("decision", UNDECIDED) != view.decision:
            continue
        if view.warehouse_id:
            lines = [line for line in it["lines"] if line["warehouse_id"] == view.warehouse_id]
            if not lines:
                continue
            qty = sum(line["qty"] for line in lines)
            it = {**it, "lines": lines, "qty": qty, "frozen": engine.frozen_money(qty, it["unit_cost"])}
        shown.append(it)
    t = stocks.thresholds
    valued = [it["frozen"] for it in shown if it["frozen"] is not None]
    return {
        "params": params_out(params), "saved": params_out(saved), "is_saved": params == saved,
        "thresholds": {"critical": t.critical, "low": t.low, "excess": t.excess},
        "sales_known": stocks.sales_known, "stocks_known": stocks.synced_at is not None,
        "sales_since": stocks.sales_since, "today": stocks.today,
        "clusters": clusters(stocks, params.excluded | saved.excluded),
        "possible": result.possible, "planned": result.planned, "left_out": result.left_out,
        "frozen": frozen_total if any(it["frozen"] is not None for it in all_items) else None,
        "without_cost": sum(1 for it in all_items if it["frozen"] is None),
        "filters": {"warehouse_id": view.warehouse_id, "priority": view.priority, "decision": view.decision},
        "warehouses": _warehouses(result.candidates), "counts": counts,
        "shown": {"items": len(shown), "qty": sum(it["qty"] for it in shown),
                  "frozen": sum(valued, Decimal(0)) if valued else None,
                  "without_cost": sum(1 for it in shown if it["frozen"] is None)},
        "cabinet_url": OZON_CABINET, "cabinet_path": CABINET_PATH,
        "items": shown,
    }


# ── вывозы, уже оформленные в кабинете Ozon ──
REMOVALS_PATH = "/v1/removal/from-stock/list"
REMOVALS_PAGE = 500
REMOVALS_MAX_PAGES = 10
REMOVALS_DAYS = (30, 90)


def _parse_dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


async def removals(db: AsyncSession, user_id: uuid.UUID, days: int = 30) -> dict:
    """Отчёт Ozon по вывозу и утилизации со стока FBO за `days` дней (бета-метод: только список, заявку не создаёт).
    Строки — как в кабинете: товар, сколько, когда создан, статус, склад, куда; товар сопоставляем по артикулу."""
    today = today_msk()
    date_from = today - timedelta(days=days - 1)
    rows, last_id = [], ""
    try:
        async with await get_ozon_client(user_id, db) as client:
            for _ in range(REMOVALS_MAX_PAGES):
                resp = await client.request("POST", REMOVALS_PATH, {
                    "date_from": date_from.isoformat(), "date_to": today.isoformat(), "last_id": last_id,
                    "limit": REMOVALS_PAGE,
                }) or {}
                page = resp.get("returns_summary_report_rows") or []
                rows.extend(page)
                last_id = resp.get("last_id") or ""
                if not last_id or len(page) < REMOVALS_PAGE:
                    break
    except OzonAPIError as exc:
        if isinstance(exc, OzonNotConfiguredError | OzonAuthError):
            raise WithdrawalError(exc.message, 400) from None
        raise WithdrawalError(f"Ozon не отдал список вывозов: {exc.message}", 502) from None
    offers = {r.get("offer_id") for r in rows if r.get("offer_id")}
    ids = dict((await db.execute(
        select(Product.offer_id, Product.id).where(Product.user_id == user_id, Product.offer_id.in_(offers))
    )).all()) if offers else {}
    out = []
    for r in rows:
        offer = r.get("offer_id") or ""
        out.append({
            "product_id": ids.get(offer), "offer_id": offer, "name": r.get("name") or None,
            "sku": str(r.get("sku") or "") or None, "qty": int(r.get("quantity_for_return") or 0),
            "created_at": _parse_dt(r.get("return_created_at")), "state": r.get("return_state") or None,
            "box_state": r.get("box_state") or None, "stock_type": r.get("stock_type") or None,
            "delivery_type": r.get("delivery_type") or None, "is_auto_return": bool(r.get("is_auto_return")),
            "warehouse": r.get("clearing_warehouse_name") or None,
            "destination": r.get("destination_warehouse_name") or None,
        })
    out.sort(key=lambda r: (r["created_at"] is None, -(r["created_at"].timestamp() if r["created_at"] else 0)))
    return {"date_from": date_from, "date_to": today, "rows": out, "qty": sum(r["qty"] for r in out),
            "cabinet_url": OZON_CABINET, "cabinet_path": CABINET_PATH}


# ── Excel для логиста ──
COLUMNS = [
    Col("Артикул", 26), Col("Название", 40), Col("Кластер", 28), Col("Склад", 26),
    Col("Сколько вывезти, шт.", 14, "int"), Col("На складе, шт.", 12, "int"), Col("Останется в кластере, шт.", 14, "int"),
    Col("Причина", 40), Col("Последняя продажа", 14, "date"), Col("Приоритет", 11),
    Col("Замороженные деньги, ₽", 14, "money"), Col("Решение", 18),
]


def _excluded_names(data: dict) -> list[str]:
    names = {c["key"]: c["name"] for c in data["clusters"]}
    return [names.get(k, f"кластер {k}") for k in data["params"]["excluded"]]


def shown_filter(data: dict) -> str:
    f = data["filters"]
    parts = []
    if f["warehouse_id"]:
        names = {w["id"]: w["name"] for w in data["warehouses"]}
        parts.append(f"склад {names.get(f['warehouse_id'], '—')}")
    if f["priority"]:
        parts.append(f"приоритет {PRIORITY_TEXT[f['priority']]}")
    if f["decision"]:
        parts.append({UNDECIDED: "без решения", REMOVE: "решено вывезти", KEEP: "решено оставить"}[f["decision"]])
    return ", ".join(parts)


def to_xlsx(data: dict) -> bytes:
    rows = []
    for it in data["items"]:
        p = it["product"]
        d = it["decision"]
        decision = f"{DECISION_TEXT[d['decision']]}, {d['at']:%d.%m.%Y}" if d else ""
        for line in it["lines"]:
            frozen = engine.frozen_money(line["qty"], it["unit_cost"])
            rows.append([
                p["offer_id"], p["name"], line["cluster"] or "—", line["warehouse"], line["qty"], line["present"],
                line["cluster_left"], it["reason"], it["last_sale"], PRIORITY_TEXT[it["priority"]],
                frozen if frozen is not None else "нет себестоимости", decision,
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
        "Причина: «Нет продаж» — за 30 дней не продавался; «Низкая оборачиваемость» — запаса больше двух порогов "
        "«Избыток»; «Избыточный запас» — больше порога. Приоритет: высокий, средний, обычный — в том же порядке.",
        "Замороженные деньги — сколько вывезти × себестоимость с НДС; нет себестоимости — не посчитаны (а не 0).",
        f"Итого: {data['planned']} шт. из {data['possible']} возможных.",
    ]
    if shown_filter(data):
        notes.append(f"В файле — по фильтру: {shown_filter(data)}; {data['shown']['qty']} шт.")
    shown = data["shown"]
    total = ["Итого", None, None, None, shown["qty"], None, None, None, None, None, shown["frozen"], None]
    return table_xlsx("Вывоз", COLUMNS, rows, total=total, notes_title="Параметры", notes=notes)
