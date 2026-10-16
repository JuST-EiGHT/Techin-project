"""Синхронизация отправлений (заказов) Ozon — продажи по кластерам и складам для планирования поставок (шаг 5.2).

FBO — /v3/posting/fbo/list (v2 отключён 31.08.2026): склад отгрузки (`analytics_data.warehouse_id`) и кластер
покупателя (`financial_data.cluster_to`). FBS и rFBS — /v4/posting/fbs/list (v3 отключён 31.08.2026): склад продавца
(`delivery_method.warehouse_id`); запрашиваем, только если у продавца есть свои склады (warehouses FBS / rFBS).
Оба метода — период по дате заказа (since / to, не больше года), курсор, по 100 отправлений.

Первый запуск — за 90 дней, дальше — последние 35 дней: так видны отмены и смена статуса за окно расчёта поставок
(30 дней). Отправление — строка навсегда: новое — вставка, у известного меняются статус и состав (`postings`).
Персональные данные покупателя (имя, телефон, адрес, юрлицо, коды активации, IMEI) не сохраняем — `raw_json` только
из разрешённых полей. Всё — одной транзакцией.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.models.posting import FBO, FBS, Posting, PostingItem, PostingSync
from app.models.product import Product
from app.models.warehouse import FBS as WH_FBS
from app.models.warehouse import RFBS, Warehouse
from app.services.sync.finance import MSK, today_msk
from app.services.sync.parsing import to_count, to_decimal, to_id, to_str
from app.services.sync.promotions import parse_dt

log = logging.getLogger(__name__)

FBO_LIST_PATH = "/v3/posting/fbo/list"
FBS_LIST_PATH = "/v4/posting/fbs/list"
PATHS = {FBO: FBO_LIST_PATH, FBS: FBS_LIST_PATH}
PAGE = 100  # максимум limit в обоих методах
MAX_PAGES = 5000
BACKFILL_DAYS = 90  # первый запуск
REFRESH_DAYS = 35  # дальше: окно расчёта поставок (30 дней) с запасом — видны отмены
_CENT = Decimal("0.01")
_LOOKUP_CHUNK = 500

# raw_json — только эти поля: без покупателя, адреса, юрлица, кодов активации и IMEI
_KEEP = (
    "posting_number", "order_id", "order_number", "status", "substatus", "created_at", "in_process_at",
    "shipment_date", "delivering_date", "delivery_schema", "is_express", "cancel_reason_id", "cancellation",
    "delivery_method", "financial_data",
)
_KEEP_ANALYTICS = ("warehouse_id", "warehouse_name", "warehouse", "delivery_type", "is_premium", "tpl_provider",
                   "tpl_provider_id", "payment_type_group_name")
_KEEP_PRODUCT = ("sku", "offer_id", "name", "quantity", "price", "is_marketplace_buyout")


@dataclass
class PostingSyncStats:
    fbo_new: int = 0
    fbo_updated: int = 0
    fbo_unchanged: int = 0
    fbs_new: int = 0
    fbs_updated: int = 0
    fbs_unchanged: int = 0
    skipped: int = 0  # без номера или даты заказа
    unknown_products: int = 0  # товары, которых нет в каталоге (нужна синхронизация товаров)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def add(self, scheme: str, what: str) -> None:
        key = f"{scheme.lower()}_{what}"
        setattr(self, key, getattr(self, key) + 1)


@dataclass
class Parsed:
    number: str
    order_number: str | None
    status: str
    ordered_at: datetime
    warehouse_id: int | None
    warehouse_name: str | None
    cluster_from: str | None
    cluster_to: str | None
    items: list[tuple[int | None, str | None, int, Decimal | None]]  # sku, артикул, штуки, цена
    raw: dict


def _money(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("amount")
    return to_decimal(value, _CENT)


def sanitize(p: dict) -> dict:
    """Ответ Ozon без персональных данных — для raw_json."""
    raw = {k: p[k] for k in _KEEP if k in p}
    if isinstance(p.get("analytics_data"), dict):
        raw["analytics_data"] = {k: v for k, v in p["analytics_data"].items() if k in _KEEP_ANALYTICS}
    raw["products"] = [
        {k: v for k, v in item.items() if k in _KEEP_PRODUCT} for item in p.get("products") or [] if isinstance(item, dict)
    ]
    return raw


def parse(p: dict, scheme: str) -> Parsed | None:
    number = to_str(p.get("posting_number"), 64)
    first, second = ("created_at", "in_process_at") if scheme == FBO else ("in_process_at", "created_at")
    ordered_at = parse_dt(p.get(first)) or parse_dt(p.get(second))
    if not number or ordered_at is None:
        return None
    if scheme == FBO:
        place = p.get("analytics_data") if isinstance(p.get("analytics_data"), dict) else {}
        warehouse_id, warehouse_name = to_id(place.get("warehouse_id")), to_str(place.get("warehouse_name"), 255)
    else:
        place = p.get("delivery_method") if isinstance(p.get("delivery_method"), dict) else {}
        warehouse_id, warehouse_name = to_id(place.get("warehouse_id")), to_str(place.get("name"), 255)
    finance = p.get("financial_data") if isinstance(p.get("financial_data"), dict) else {}
    merged: dict[tuple[int | None, str | None], list] = {}
    for item in p.get("products") or []:
        if not isinstance(item, dict):
            continue
        key = (to_id(item.get("sku")), to_str(item.get("offer_id"), 100))
        if key == (None, None):
            continue
        row = merged.setdefault(key, [0, _money(item.get("price"))])
        row[0] += to_count(item.get("quantity"))
    return Parsed(
        number=number, order_number=to_str(p.get("order_number"), 64), status=to_str(p.get("status"), 40) or "unknown",
        ordered_at=ordered_at, warehouse_id=warehouse_id, warehouse_name=warehouse_name,
        cluster_from=to_str(finance.get("cluster_from"), 255), cluster_to=to_str(finance.get("cluster_to"), 255),
        items=[(sku, offer, qty, price) for (sku, offer), (qty, price) in merged.items()], raw=sanitize(p),
    )


def is_cancelled(status: str) -> bool:
    return status.startswith("cancelled") or status == "not_accepted"


# ── запросы к Ozon ──
async def fetch(client: OzonClient, scheme: str, since: datetime, to: datetime) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        body = {
            "filter": {"since": since.isoformat().replace("+00:00", "Z"), "to": to.isoformat().replace("+00:00", "Z")},
            "limit": PAGE, "cursor": cursor, "sort_dir": "ASC",
            "with": {"analytics_data": True, "financial_data": True},
        }
        data = await client.request("POST", PATHS[scheme], body)
        page = data.get("postings") if isinstance(data, dict) else None
        out.extend(p for p in page or [] if isinstance(p, dict))
        cursor = (data.get("cursor") or "") if isinstance(data, dict) else ""
        has_next = isinstance(data, dict) and data.get("has_next") in (True, "true")
        if not page or not cursor or not has_next:
            break
    return out


# ── БД ──
def _signature(p: Posting | Parsed) -> tuple:
    if isinstance(p, Posting):
        items = sorted((i.sku or 0, i.offer_id or "", i.quantity, i.price) for i in p.items)
        when = p.ordered_at if p.ordered_at.tzinfo else p.ordered_at.replace(tzinfo=timezone.utc)
        return (p.status, p.warehouse_id, p.warehouse_name, p.cluster_from, p.cluster_to, when, items)
    items = sorted((sku or 0, offer or "", qty, price) for sku, offer, qty, price in p.items)
    return (p.status, p.warehouse_id, p.warehouse_name, p.cluster_from, p.cluster_to, p.ordered_at, items)


def _fill(posting: Posting, parsed: Parsed, now: datetime, products: "_Catalog", stats: PostingSyncStats) -> None:
    posting.order_number, posting.status = parsed.order_number, parsed.status
    posting.ordered_at, posting.day = parsed.ordered_at, parsed.ordered_at.astimezone(MSK).date()
    posting.warehouse_id, posting.warehouse_name = parsed.warehouse_id, parsed.warehouse_name
    posting.cluster_from, posting.cluster_to = parsed.cluster_from, parsed.cluster_to
    posting.raw_json, posting.synced_at = parsed.raw, now
    posting.items.clear()
    for sku, offer, qty, price in parsed.items:
        product_id = products.find(sku, offer)
        stats.unknown_products += product_id is None
        posting.items.append(PostingItem(sku=sku, offer_id=offer, product_id=product_id, quantity=qty, price=price))


class _Catalog:
    def __init__(self, products: list[Product]) -> None:
        self.by_sku = {p.ozon_sku: p.id for p in products if p.ozon_sku}
        self.by_offer = {p.offer_id: p.id for p in products}

    def find(self, sku: int | None, offer: str | None) -> uuid.UUID | None:
        return self.by_sku.get(sku) or self.by_offer.get(offer)


async def _existing(db: AsyncSession, user_id: uuid.UUID, numbers: list[str]) -> dict[str, Posting]:
    out: dict[str, Posting] = {}
    for i in range(0, len(numbers), _LOOKUP_CHUNK):
        chunk = numbers[i:i + _LOOKUP_CHUNK]
        res = await db.execute(select(Posting).where(Posting.user_id == user_id, Posting.posting_number.in_(chunk)))
        out.update({p.posting_number: p for p in res.scalars()})
    return out


async def _save(
    db: AsyncSession, user_id: uuid.UUID, scheme: str, rows: list[dict], catalog: _Catalog, now: datetime,
    stats: PostingSyncStats,
) -> None:
    parsed: dict[str, Parsed] = {}
    for row in rows:
        item = parse(row, scheme)
        if item is None:
            stats.skipped += 1
            continue
        parsed[item.number] = item  # одно отправление дважды (страницы сдвинулись) — берём последнее
    existing = await _existing(db, user_id, list(parsed))
    for number, item in parsed.items():
        posting = existing.get(number)
        if posting is None:
            posting = Posting(user_id=user_id, scheme=scheme, posting_number=number, first_seen_at=now)
            _fill(posting, item, now, catalog, stats)
            db.add(posting)
            stats.add(scheme, "new")
        elif _signature(posting) != _signature(item):
            _fill(posting, item, now, catalog, stats)
            stats.add(scheme, "updated")
        else:
            posting.synced_at = now
            stats.add(scheme, "unchanged")


def _start(day: date) -> datetime:
    return datetime.combine(day, time(), MSK).astimezone(timezone.utc)


async def sync_postings(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> PostingSyncStats:
    stats = PostingSyncStats()
    now = datetime.now(timezone.utc)
    today = today_msk()
    catalog = _Catalog(list((await db.execute(select(Product).where(Product.user_id == user_id))).scalars()))
    own = await db.scalar(
        select(Warehouse.id).where(Warehouse.user_id == user_id, Warehouse.type.in_((WH_FBS, RFBS))).limit(1)
    )
    schemes = [FBO] + ([FBS] if own is not None else [])
    for scheme in schemes:
        state = await db.get(PostingSync, (user_id, scheme))
        since_day = today - timedelta(days=(REFRESH_DAYS if state else BACKFILL_DAYS) - 1)
        rows = await fetch(client, scheme, _start(since_day), now)
        await _save(db, user_id, scheme, rows, catalog, now, stats)
        if state is None:
            db.add(PostingSync(user_id=user_id, scheme=scheme, loaded_from=since_day, synced_at=now))
        else:
            state.loaded_from = min(state.loaded_from, since_day)
            state.synced_at = now
    await db.commit()
    log.info("Синхронизация отправлений: пользователь %s → %s", user_id, stats.as_dict())
    return stats
