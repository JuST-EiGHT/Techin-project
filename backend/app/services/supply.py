"""Планирование поставок (шаг 5.2): что, куда и сколько везти — по отправлениям Ozon и текущим остаткам.

Спрос — заказы без отмен (`postings`, шаг 5.2): FBO — по кластеру покупателя (`cluster_to`; нет — кластер склада
отгрузки), FBS / rFBS — по складу продавца. Окно — 30 полных дней до сегодня (и последние 7 из них для тренда), но не
раньше, чем загружены отправления, и не позже последней синхронизации. Остаток: кластер — сумма по складам Ozon этого
кластера (доступно + в пути), свой склад — доступно минус резерв. Расчёт — engine/supply_planner (BUSINESS_LOGIC §5).
Параметры — настройки пользователя (запас 45 дней, 7 дней в пути); в запросе их можно переопределить, не сохраняя.
"""
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

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
from app.services.stocks import latest_stocks
from app.services.sync.finance import MSK, today_msk
from app.services.sync.postings import is_cancelled
from app.services.user_settings import get_settings, load_thresholds, supply_params
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
    target, lead = supply_params(await get_settings(db, user_id))
    return engine.Params(target_days=target, lead_time_days=lead)


def with_overrides(base: engine.Params, *, target_days: int | None, lead_time_days: int | None) -> engine.Params:
    changes = {k: v for k, v in (("target_days", target_days), ("lead_time_days", lead_time_days)) if v is not None}
    return replace(base, **changes)


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


def coverage(state: PostingSync, today: date) -> Coverage | None:
    """Окно расчёта: 30 полных дней до сегодня — но с первого загруженного дня и до последнего полного дня перед
    синхронизацией (день синхронизации загружен не целиком)."""
    synced_day = utc(state.synced_at).astimezone(MSK).date()
    end = min(today - timedelta(days=1), synced_day - timedelta(days=1))
    start = max(end - timedelta(days=WINDOW_DAYS - 1), state.loaded_from)
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
    covs = {scheme: coverage(state, today) for scheme, state in states.items()}
    clusters, own = await _destinations(db, user_id)

    points: list[engine.Point] = []
    places: dict[tuple[str, str], Destination] = {}
    for scheme, cov in covs.items():
        if cov is None or cov.days_30 < engine.MIN_DAYS:
            continue
        known = clusters if scheme == FBO else own
        kind = engine.SUPPLY if scheme == FBO else engine.PURCHASE
        for (product_id, key), (sold, sold_7, name) in (await _demand(db, user_id, cov, catalog)).items():
            dest = known.get(key)
            if dest is None:  # покупают там, где товара нет и склада нет — везти туда с нуля
                dest = known.setdefault(key, Destination(kind, key, name or key, stock={}))
            stock, transit, _ = dest.stock.get(product_id, (0, 0, []))
            places[(kind, key)] = dest
            points.append(engine.Point(
                product=product_id, offer_id=products[product_id].offer_id, kind=kind, destination=key,
                stock=stock, in_transit=transit, demand=engine.Demand(sold, sold_7, cov.days_30, cov.days_7),
            ))
    recs = engine.plan(points, params, thresholds)

    items = []
    for r in recs:
        p = products[r.point.product]
        dest = places[(r.point.kind, r.point.destination)]
        lying = dest.stock.get(p.id, (0, 0, []))[2] if r.point.kind == engine.SUPPLY else []
        items.append({
            "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name, "ozon_sku": p.ozon_sku},
            "kind": r.point.kind, "urgency": r.urgency,
            "destination": {"key": dest.key, "name": dest.name, "warehouse_type": dest.warehouse_type,
                            "warehouses": sorted(lying)},
            "sold_30": r.point.demand.sold_30, "sold_7": r.point.demand.sold_7,
            "avg_daily": r.avg_daily, "rate": r.rate, "trend": r.trend, "stock": r.point.stock,
            "in_transit": r.point.in_transit, "days_left": r.days_left, "target_stock": r.target_stock,
            "need": r.need, "to_order": r.to_order, "arrives_late": r.arrives_late,
        })
    totals = {u: sum(1 for r in recs if r.urgency == u) for u in engine.URGENCIES}
    fbo, fbs = covs.get(FBO), covs.get(FBS)
    return {
        "params": {"target_days": params.target_days, "lead_time_days": params.lead_time_days},
        "saved": {"target_days": saved.target_days, "lead_time_days": saved.lead_time_days},
        "is_saved": params == saved,
        "thresholds": {"critical": thresholds.critical, "low": thresholds.low, "excess": thresholds.excess},
        "has_key": await crud_credential.has_active(db, user_id),
        "postings_known": FBO in states,
        "fbo": fbo.out() if fbo else None,
        "fbs": fbs.out() if fbs else None,
        "has_own_warehouses": bool(own),
        "totals": {**totals, "supply_units": sum(r.to_order for r in recs if r.point.kind == engine.SUPPLY),
                   "purchase_units": sum(r.to_order for r in recs if r.point.kind == engine.PURCHASE),
                   "products": len({r.point.product for r in recs})},
        "today": today,
        "items": items,
    }


# ── Excel для логиста ──
COLUMNS = [
    Col("Тип", 22), Col("Куда", 30), Col("Срочность", 11), Col("Артикул", 26), Col("Название", 36),
    Col("Отправить / закупить, шт.", 14, "int"), Col("Сейчас там, шт.", 11, "int"), Col("В пути, шт.", 10, "int"),
    Col("Хватит на, дн.", 10, "number"), Col("Продажи в день (30 дн.)", 12, "number"),
    Col("Продажи в день (7 дн.)", 12, "number"), Col("Запас после поставки, шт.", 13, "int"),
]


def to_xlsx(data: dict) -> bytes:
    """Строки — по типу и месту (удобно собирать поставку в один кластер), внутри — срочные первыми."""
    rank = {u: i for i, u in enumerate(engine.URGENCIES)}
    items = sorted(data["items"], key=lambda i: (
        i["kind"] != engine.SUPPLY, i["destination"]["name"], rank[i["urgency"]], i["product"]["offer_id"],
    ))
    rows = [
        [KIND_TEXT[i["kind"]], i["destination"]["name"], URGENCY_TEXT[i["urgency"]], i["product"]["offer_id"],
         i["product"]["name"], i["to_order"], i["stock"], i["in_transit"], i["days_left"], i["avg_daily"], i["rate"],
         i["target_stock"]]
        for i in items
    ]
    prm, t = data["params"], data["thresholds"]
    supply, purchase = data["totals"]["supply_units"], data["totals"]["purchase_units"]
    notes = [
        f"Планирование поставок — {data['today']:%d.%m.%Y}",
        "",
        f"Запас после поставки: на {prm['target_days']} дн. продаж; поставка в пути: {prm['lead_time_days']} дн.",
        f"Срочно — запаса меньше чем на {t['critical']} дн., скоро — меньше чем на {t['low']} дн.",
        "",
        "Продажи — заказы без отмен за последние 30 дней (и 7 дней — тренд): для складов Ozon — по кластеру покупателя, "
        "для своих складов — по складу отгрузки.",
        "Хватит на — остаток сейчас / продажи в день за 7 дней. Отправить — продажи в день за 30 дней × (запас + дни в "
        "пути) минус остаток и то, что уже в пути.",
        "Поставка в Ozon — в кластер (Ozon сам распределит по складам кластера); закупка — на свой склад (FBS / rFBS).",
        f"Итого: в Ozon — {supply} шт., закупить на свои склады — {purchase} шт.",
    ]
    total = ["Итого", None, None, None, None, supply + purchase, None, None, None, None, None, None]
    return table_xlsx("Поставки", COLUMNS, rows, total=total, notes_title="Параметры", notes=notes)
