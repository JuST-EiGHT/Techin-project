"""Планирование поставок (шаг 5.2): что, куда и сколько везти — по отправлениям Ozon и текущим остаткам.

Спрос — заказы без отмен (`postings`, шаг 5.2): FBO — по кластеру покупателя (`cluster_to`; нет — кластер склада
отгрузки), FBS / rFBS — по складу продавца. Окно — 30 полных дней до сегодня (и последние 7 из них для тренда), но не
раньше, чем загружены отправления, и не позже последней синхронизации. Остаток: кластер — сумма по складам Ozon этого
кластера (доступно + в пути), свой склад — доступно минус резерв. Расчёт — engine/supply_planner (BUSINESS_LOGIC §5).
Параметры — настройки пользователя (запас 45 дней, 7 дней в пути); в запросе их можно переопределить, не сохраняя.

10.18: страховой запас (дней, по умолчанию 0) добавляется к цели; у рекомендации — сколько уже в открытых планах сервиса и в
заявках Ozon, которые ещё готовятся (не в пути — в остатках «в пути» их нет), и сколько нужно ещё.

10.18a: склады Ozon — по данным Ozon о кластере, сведённым по id кластера размещения (`macrolocal_cluster_id`), а не по
названию: остаток и в пути — последние снимки складов кластера, продажи в день — `ads_cluster` (`cluster_stock_metrics`),
рекомендованная поставка Ozon — `ozon_supply_recs`; расчёт — `engine.cluster_recommend`. Ключ места в ответе — по названию
кластера (как раньше: планы и заявки 10.18 им пользуются). Склад кластера, куда везти, и минимум на склад — настройки.
"""
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.engine import supply_planner as engine
from app.models.posting import FBO, FBS, Posting, PostingItem, PostingSync
from app.models.product import Product
from app.models.warehouse import FBO as WH_FBO
from app.models.warehouse import FBS as WH_FBS
from app.models.warehouse import RFBS, Warehouse
from app.schemas.sync import utc
from app.models.cluster_metrics import OzonSupplyRecSync
from app.services.stocks import latest_stocks
from app.services.sync.finance import MSK, today_msk
from app.services.sync.postings import is_cancelled
from app.services.sync.stocks import latest_cluster_metrics
from app.services.sync.supply_recs import latest_recs
from app.services.user_settings import (
    get_settings,
    load_thresholds,
    supply_cluster_warehouses,
    supply_demand_days,
    supply_min_qty,
    supply_params,
    supply_safety_days,
)
from app.services.xlsx_export import Col, table_xlsx

WINDOW_DAYS = 30
TREND_DAYS = 7
URGENCY_TEXT = {engine.CRITICAL: "Срочно", engine.URGENT: "Скоро", engine.PLANNED: "Плановая"}
KIND_TEXT = {engine.SUPPLY: "Поставка в Ozon", engine.PURCHASE: "Закупка на свой склад"}


def cluster_key(name: str | None) -> str | None:
    """Кластер по названию: регистр, «ё», лишние пробелы не важны (в остатках и в заказах Ozon пишет одинаково)."""
    if not name or not name.strip():
        return None
    return " ".join(name.lower().replace("ё", "е").split())


# ── параметры ──
async def load_params(db: AsyncSession, user_id: uuid.UUID) -> engine.Params:
    s = await get_settings(db, user_id)
    target, lead = supply_params(s)
    return engine.Params(target_days=target, lead_time_days=lead, safety_days=supply_safety_days(s))


def with_overrides(base: engine.Params, *, target_days: int | None, lead_time_days: int | None,
                   safety_days: int | None = None) -> engine.Params:
    changes = {k: v for k, v in (("target_days", target_days), ("lead_time_days", lead_time_days),
                                 ("safety_days", safety_days)) if v is not None}
    return replace(base, **changes)


def params_out(p: engine.Params) -> dict:
    return {"target_days": p.target_days, "lead_time_days": p.lead_time_days, "safety_days": p.safety_days}


# ── покрытие данными ──
@dataclass
class Coverage:
    scheme: str
    loaded_from: date
    synced_at: datetime
    window_from: date  # окно 30 дней с учётом загруженного
    window_to: date
    days_30: int
    trend_from: date
    days_7: int

    def out(self) -> dict:
        return {
            "loaded_from": self.loaded_from, "synced_at": utc(self.synced_at), "window_from": self.window_from,
            "window_to": self.window_to, "days": self.days_30, "enough": self.days_30 >= engine.MIN_DAYS,
        }


def coverage(state: PostingSync, today: date, window: int = WINDOW_DAYS) -> Coverage | None:
    """Окно расчёта: `window` полных дней до сегодня (период планирования — настройка, 30 по умолчанию, шаг 10.26) — но
    с первого загруженного дня и до последнего полного дня перед синхронизацией (день синхронизации загружен не
    целиком). `days_30` — дней в окне (имя — с шага 5.2, когда окно было всегда 30 дней)."""
    synced_day = utc(state.synced_at).astimezone(MSK).date()
    end = min(today - timedelta(days=1), synced_day - timedelta(days=1))
    start = max(end - timedelta(days=window - 1), state.loaded_from)
    if end < start:
        return None
    trend_from = max(end - timedelta(days=TREND_DAYS - 1), start)
    return Coverage(
        scheme=state.scheme, loaded_from=state.loaded_from, synced_at=state.synced_at, window_from=start, window_to=end,
        days_30=(end - start).days + 1, trend_from=trend_from, days_7=(end - trend_from).days + 1,
    )


# ── расчёт ──
@dataclass
class Destination:
    kind: str  # supply | purchase
    key: str
    name: str
    warehouse_type: str | None = None  # FBS | rFBS — у своего склада
    stock: dict | None = None  # product_id → (остаток, в пути, склады Ozon кластера, где этот товар лежит)


async def _demand(
    db: AsyncSession, user_id: uuid.UUID, cov: Coverage, catalog: dict
) -> dict[tuple[uuid.UUID, str], list]:
    """(товар, куда) → [продано за окно, продано за 7 дней, название места из заказов]."""
    rows = await db.execute(
        select(Posting.day, Posting.status, Posting.cluster_to, Posting.cluster_from, Posting.warehouse_id,
               Posting.warehouse_name, PostingItem.product_id, PostingItem.sku, PostingItem.offer_id, PostingItem.quantity)
        .join(PostingItem, PostingItem.posting_id == Posting.id)
        .where(Posting.user_id == user_id, Posting.scheme == cov.scheme,
               Posting.day >= cov.window_from, Posting.day <= cov.window_to)
    )
    out: dict[tuple[uuid.UUID, str], list] = {}
    for day, status, cluster_to, cluster_from, wid, wname, product_id, sku, offer, qty in rows:
        if is_cancelled(status) or qty <= 0:
            continue
        product_id = product_id or catalog["by_sku"].get(sku) or catalog["by_offer"].get(offer)
        if product_id is None:
            continue
        if cov.scheme == FBO:
            name = cluster_to or cluster_from
            key = cluster_key(name)
        else:
            name, key = wname, str(wid) if wid else None
        if key is None:
            continue
        acc = out.setdefault((product_id, key), [0, 0, name])
        acc[0] += qty
        if day >= cov.trend_from:
            acc[1] += qty
    return out


async def _destinations(db: AsyncSession, user_id: uuid.UUID) -> tuple[dict[str, Destination], dict[str, Destination]]:
    """Кластеры Ozon и свои склады с текущими остатками товаров."""
    clusters: dict[str, Destination] = {}
    own: dict[str, Destination] = {}
    for w in (await db.execute(select(Warehouse).where(Warehouse.user_id == user_id))).scalars():
        if w.type == WH_FBO and (key := cluster_key(w.cluster_name)):
            clusters.setdefault(key, Destination(engine.SUPPLY, key, w.cluster_name, stock={}))
        elif w.type in (WH_FBS, RFBS):
            own[str(w.ozon_warehouse_id)] = Destination(engine.PURCHASE, str(w.ozon_warehouse_id), w.name, w.type, stock={})
    for s, p, w in await latest_stocks(db, user_id):
        if w.type == WH_FBO:
            d = clusters.get(cluster_key(w.cluster_name) or "")
            if d is None:
                continue
            present, transit, names = d.stock.get(p.id, (0, 0, []))
            d.stock[p.id] = (present + max(s.present, 0), transit + max(s.in_transit or 0, 0),
                             names + [w.name] if s.present > 0 else names)
        elif (d := own.get(str(w.ozon_warehouse_id))) is not None:
            free = max(s.present - (s.reserved or 0), 0)  # зарезервированное уже продано — это спрос, не запас
            present, transit, names = d.stock.get(p.id, (0, 0, []))
            d.stock[p.id] = (present + free, transit, names)
    return clusters, own


# ── 10.18a: склады Ozon по данным Ozon о кластере ──
REASON_TEXT = {
    "NO_SUPPLIES_TO_CLUSTER": "в кластер не возили — заказы едут издалека",
    "NOT_ENOUGH_STOCK": "в кластере не хватает остатка",
}


@dataclass
class OzonCluster:
    gid: tuple  # ("m", macrolocal_cluster_id) или ("c", cluster_id), если кластера размещения Ozon не сообщил
    name: str
    key: str  # cluster_key(name) — как ключи планов и заявок 10.18
    stock: dict  # товар → [доступно, в пути, склады с остатком]
    ads: dict  # товар → продажи в день (сумма по кластерам Ozon внутри кластера размещения)
    grade: dict  # товар → статус ликвидности Ozon в кластере
    warehouses: dict  # id склада Ozon → название (наши склады этого кластера)


async def ozon_clusters(db: AsyncSession, user_id: uuid.UUID) -> tuple[dict[tuple, OzonCluster], dict, dict]:
    """Кластеры Ozon с остатками и продажами по товарам; рекомендации Ozon — (товар, кластер) → (шт., внимание, причины);
    сопоставление кластера Ozon (`cluster_id`) с кластером размещения."""
    macro_of: dict[int, int] = {}
    names: dict[tuple, str] = {}
    clusters: dict[tuple, OzonCluster] = {}
    warehouses = [w for w in (await db.execute(
        select(Warehouse).where(Warehouse.user_id == user_id, Warehouse.type == WH_FBO)
    )).scalars()]
    metrics = await latest_cluster_metrics(db, user_id)
    for w in warehouses:
        if w.cluster_id is not None and w.macrolocal_cluster_id is not None:
            macro_of[w.cluster_id] = w.macrolocal_cluster_id
    for m in metrics.values():
        if m.macrolocal_cluster_id is not None:
            macro_of.setdefault(m.cluster_id, m.macrolocal_cluster_id)

    def gid(macro: int | None, cluster_id: int | None) -> tuple | None:
        macro = macro if macro is not None else macro_of.get(cluster_id) if cluster_id is not None else None
        if macro is not None:
            return ("m", macro)
        return ("c", cluster_id) if cluster_id is not None else None

    def cluster(g: tuple, name: str | None) -> OzonCluster:
        if g not in clusters:
            title = names.get(g) or name or f"Кластер {g[1]}"
            clusters[g] = OzonCluster(g, title, cluster_key(title) or str(g[1]), {}, {}, {}, {})
        return clusters[g]

    for w in warehouses:  # название кластера — как у наших складов: так же его пишут заявки 10.18
        if (g := gid(w.macrolocal_cluster_id, w.cluster_id)) is not None and w.cluster_name:
            names.setdefault(g, w.cluster_name)
    for w in warehouses:
        if (g := gid(w.macrolocal_cluster_id, w.cluster_id)) is not None:
            cluster(g, w.cluster_name).warehouses[w.ozon_warehouse_id] = w.name
    for s, p, w in await latest_stocks(db, user_id):
        if w.type != WH_FBO or (g := gid(w.macrolocal_cluster_id, w.cluster_id)) is None:
            continue
        row = cluster(g, w.cluster_name).stock.setdefault(p.id, [0, 0, []])
        row[0] += max(s.present, 0)
        row[1] += max(s.in_transit or 0, 0)
        if s.present > 0:
            row[2].append(w.name)
    for m in metrics.values():
        if (g := gid(m.macrolocal_cluster_id, m.cluster_id)) is None:
            continue
        c = cluster(g, m.cluster_name)
        if m.ads is not None:
            c.ads[m.product_id] = c.ads.get(m.product_id, Decimal(0)) + Decimal(m.ads)
        if m.turnover_grade:
            c.grade.setdefault(m.product_id, m.turnover_grade)
    recs: dict[tuple, tuple] = {}
    by_sku = {p.ozon_sku: p.id for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()
              if p.ozon_sku}
    for r in (await latest_recs(db, user_id)).values():
        product_id = r.product_id or by_sku.get(r.sku)
        if product_id is None:
            continue
        g = ("m", r.macrolocal_cluster_id)
        cluster(g, r.cluster_name)
        recs[(product_id, g)] = (r.recommended, r.attention_level, [x.get("reason") for x in r.reasons or []])
    return clusters, recs, macro_of


async def macro_for_key(db: AsyncSession, user_id: uuid.UUID, key: str) -> int | None:
    """Кластер размещения Ozon по ключу места плана — из наших данных, без сравнения названий (для заявки в Ozon)."""
    oz, _, _ = await ozon_clusters(db, user_id)
    return next((c.gid[1] for c in oz.values() if c.key == key and c.gid[0] == "m"), None)


async def cluster_choices(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Кластеры с нашими складами — для настройки «склад кластера, куда везти» (у владельца — СПб → Бугры)."""
    oz, _, _ = await ozon_clusters(db, user_id)
    preferred = supply_cluster_warehouses(await get_settings(db, user_id))
    out = [
        {"key": c.key, "name": c.name, "preferred_id": preferred.get(c.key),
         "warehouses": [{"id": wid, "name": name} for wid, name in sorted(c.warehouses.items(), key=lambda x: x[1])]}
        for c in oz.values() if c.warehouses
    ]
    return sorted(out, key=lambda c: c["name"])


async def report(db: AsyncSession, user_id: uuid.UUID, params: engine.Params) -> dict:
    today = today_msk()
    thresholds = await load_thresholds(db, user_id)
    saved = await load_params(db, user_id)
    products = {p.id: p for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()}
    catalog = {
        "by_sku": {p.ozon_sku: p.id for p in products.values() if p.ozon_sku},
        "by_offer": {p.offer_id: p.id for p in products.values()},
    }
    states = {s.scheme: s for s in (await db.execute(select(PostingSync).where(PostingSync.user_id == user_id))).scalars()}
    settings = await get_settings(db, user_id)
    covs = {scheme: coverage(state, today, supply_demand_days(settings)) for scheme, state in states.items()}
    _, own = await _destinations(db, user_id)  # кластеры Ozon — ниже, по id; отсюда — свои склады

    min_qty = supply_min_qty(settings)
    preferred = supply_cluster_warehouses(settings)
    oz, ozon_recs, _ = await ozon_clusters(db, user_id)
    rec_state = await db.get(OzonSupplyRecSync, user_id)
    cluster_points: list[engine.ClusterPoint] = []
    by_key: dict[str, OzonCluster] = {}
    for c in oz.values():
        by_key.setdefault(c.key, c)
        for product_id in set(c.ads) | {pid for pid, g in ozon_recs if g == c.gid}:
            if product_id not in products:
                continue
            stock, transit, _ = c.stock.get(product_id, [0, 0, []])
            rec = ozon_recs.get((product_id, c.gid))
            cluster_points.append(engine.ClusterPoint(
                product=product_id, offer_id=products[product_id].offer_id, destination=c.key, stock=stock,
                in_transit=transit, ads=c.ads.get(product_id),
                ozon_rec=rec[0] if rec else (0 if rec_state is not None and rec_state.ok else None),
                attention=rec[1] if rec else None,
            ))

    points: list[engine.Point] = []
    places: dict[tuple[str, str], Destination] = {}
    for scheme, cov in covs.items():
        if scheme == FBO or cov is None or cov.days_30 < engine.MIN_DAYS:
            continue  # 10.18a: склады Ozon — по данным Ozon о кластере, заказы — только для своих складов
        kind = engine.PURCHASE
        for (product_id, key), (sold, sold_7, name) in (await _demand(db, user_id, cov, catalog)).items():
            dest = own.get(key)
            if dest is None:  # отгружали со склада, которого в остатках уже нет
                dest = own.setdefault(key, Destination(kind, key, name or key, stock={}))
            stock, transit, _ = dest.stock.get(product_id, (0, 0, []))
            places[(kind, key)] = dest
            points.append(engine.Point(
                product=product_id, offer_id=products[product_id].offer_id, kind=kind, destination=key,
                stock=stock, in_transit=transit, demand=engine.Demand(sold, sold_7, cov.days_30, cov.days_7),
            ))
    recs = engine.sort_recs(
        engine.plan(points, params, thresholds) + engine.plan_clusters(cluster_points, params, thresholds, min_qty)
    )
    from app.services import supply_plans  # планы и заявки — свой модуль, он считает по этому же расчёту (10.18)

    in_plans = await supply_plans.open_plan_qty(db, user_id)
    in_orders = await supply_plans.preparing_order_qty(db, user_id)

    items = []
    for r in recs:
        p = products[r.point.product]
        extra: dict = {}
        if r.point.kind == engine.SUPPLY:
            c = by_key[r.point.destination]
            lying = c.stock.get(p.id, [0, 0, []])[2]
            wid = preferred.get(c.key)
            rec = ozon_recs.get((p.id, c.gid))
            dest = {"key": c.key, "name": c.name, "warehouse_type": None, "warehouses": sorted(lying),
                    "warehouse": {"id": wid, "name": c.warehouses.get(wid) or f"склад {wid}"} if wid else None}
            extra = {
                "ozon_recommended": r.ozon_recommended, "own_need": r.own_need, "basis": r.basis,
                "min_applied": r.min_applied, "days_after": r.days_after, "ozon_grade": c.grade.get(p.id),
                "ozon_reasons": [REASON_TEXT.get(x, x) for x in (rec[2] if rec else []) if x],
            }
        else:
            d = places[(r.point.kind, r.point.destination)]
            dest = {"key": d.key, "name": d.name, "warehouse_type": d.warehouse_type, "warehouses": [], "warehouse": None}
        demand = r.point.demand
        items.append({
            "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku},
            "kind": r.point.kind, "urgency": r.urgency, "destination": dest, **extra,
            "sold_30": demand.sold_30 if demand else None, "sold_7": demand.sold_7 if demand else None,
            "avg_daily": r.avg_daily, "rate": r.rate, "trend": r.trend, "stock": r.point.stock,
            "in_transit": r.point.in_transit, "days_left": r.days_left, "target_stock": r.target_stock,
            "need": r.need, "to_order": r.to_order, "arrives_late": r.arrives_late,
            "in_plans": (planned := in_plans.get((r.point.kind, dest["key"], p.id), 0)),
            "in_orders": (ordered := in_orders.get((dest["key"], p.id), 0) if r.point.kind == engine.SUPPLY else 0),
            "still_needed": max(0, r.to_order - planned - ordered),
        })
    totals = {u: sum(1 for r in recs if r.urgency == u) for u in engine.URGENCIES}
    fbo, fbs = covs.get(FBO), covs.get(FBS)
    return {
        "params": params_out(params),
        "saved": params_out(saved),
        "is_saved": params == saved,
        "thresholds": {"critical": thresholds.critical, "low": thresholds.low, "excess": thresholds.excess},
        "has_key": await crud_credential.has_active(db, user_id),
        "postings_known": FBO in states,
        "fbo": fbo.out() if fbo else None,
        "fbs": fbs.out() if fbs else None,
        "has_own_warehouses": bool(own),
        # 10.18a: данные Ozon для складов Ozon — продажи по кластерам (из остатков) и рекомендации поставки
        "ozon_data": {
            "clusters_known": any(c.ads for c in oz.values()),
            "recs_known": rec_state is not None and rec_state.synced_at is not None,
            "recs_ok": rec_state.ok if rec_state else None,
            "recs_synced_at": utc(rec_state.synced_at) if rec_state and rec_state.synced_at else None,
            "recs_error": rec_state.error if rec_state and not rec_state.ok else None,
        },
        "min_qty": min_qty,
        "totals": {**totals, "supply_units": sum(r.to_order for r in recs if r.point.kind == engine.SUPPLY),
                   "purchase_units": sum(r.to_order for r in recs if r.point.kind == engine.PURCHASE),
                   "products": len({r.point.product for r in recs})},
        "today": today,
        "items": items,
    }


# ── Excel для логиста ──
COLUMNS = [
    Col("Тип", 22), Col("Куда", 30), Col("Склад", 24), Col("Срочность", 11), Col("Артикул", 26), Col("Название", 36),
    Col("Отправить / закупить, шт.", 14, "int"), Col("Рекомендует Ozon, шт.", 12, "int"), Col("По продажам, шт.", 12, "int"),
    Col("Сейчас там, шт.", 11, "int"), Col("В пути, шт.", 10, "int"), Col("Хватит на, дн.", 10, "number"),
    Col("Продажи в день", 11, "number"), Col("Хватит после поставки, дн.", 13, "number"),
    Col("Уже в планах, шт.", 11, "int"), Col("Уже в заявках Ozon, шт.", 12, "int"), Col("Нужно ещё, шт.", 11, "int"),
]


def to_xlsx(data: dict) -> bytes:
    """Строки — по типу и месту (удобно собирать поставку в один кластер): кластеры с лучшими продажами — первыми, внутри —
    срочные первыми."""
    rank = {u: i for i, u in enumerate(engine.URGENCIES)}
    sales: dict[str, Decimal] = {}
    for i in data["items"]:
        sales[i["destination"]["key"]] = sales.get(i["destination"]["key"], Decimal(0)) + Decimal(i["avg_daily"] or 0)
    items = sorted(data["items"], key=lambda i: (
        i["kind"] != engine.SUPPLY, -sales[i["destination"]["key"]], i["destination"]["name"], rank[i["urgency"]],
        i["product"]["offer_id"],
    ))
    rows = [
        [KIND_TEXT[i["kind"]], i["destination"]["name"],
         (i["destination"].get("warehouse") or {}).get("name") or ("любой склад кластера" if i["kind"] == engine.SUPPLY else ""),
         URGENCY_TEXT[i["urgency"]], i["product"]["offer_id"], i["product"]["name"], i["to_order"],
         i.get("ozon_recommended"), i.get("own_need"), i["stock"], i["in_transit"], i["days_left"], i["avg_daily"],
         i.get("days_after"), i["in_plans"], i["in_orders"], i["still_needed"]]
        for i in items
    ]
    prm, t = data["params"], data["thresholds"]
    supply, purchase = data["totals"]["supply_units"], data["totals"]["purchase_units"]
    horizon = prm["target_days"] + prm["safety_days"] + prm["lead_time_days"]
    notes = [
        f"Планирование поставок — {data['today']:%d.%m.%Y}",
        "",
        f"Запас после поставки: на {prm['target_days']} дн. продаж, страховой — {prm['safety_days']} дн.; поставка в "
        f"пути: {prm['lead_time_days']} дн.; минимум одного товара на склад — {data['min_qty']} шт.",
        f"Срочно — запаса меньше чем на {t['critical']} дн., скоро — меньше чем на {t['low']} дн.",
        "",
        f"Склады Ozon: продажи в день — по данным Ozon в кластере (28 дней). По продажам = продажи в день × {horizon} дн. "
        "минус остаток в кластере и то, что уже едет. Отправить — не меньше, чем рекомендует Ozon (это необходимый "
        "минимум), и не меньше минимума на склад.",
        "Поставка в Ozon — в кластер; склад — если задан в настройках, иначе Ozon выберет сам. Закупка на свой склад "
        "(FBS / rFBS) — по заказам за период планирования.",
        "Уже в планах — в открытых планах поставки сервиса; уже в заявках — в заявках Ozon, которые ещё готовятся. "
        "Нужно ещё = отправить − в планах − в заявках.",
        f"Итого: в Ozon — {supply} шт., закупить на свои склады — {purchase} шт.",
    ]
    total = ["Итого", None, None, None, None, None, supply + purchase, None, None, None, None, None, None, None, None,
             None, sum(i["still_needed"] for i in items)]
    return table_xlsx("Поставки", COLUMNS, rows, total=total, notes_title="Параметры", notes=notes)
