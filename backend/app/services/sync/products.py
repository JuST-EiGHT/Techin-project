"""Синхронизация товаров и цен из Ozon в БД.

1. /v3/product/list — все product_id (пагинация по last_id; видимость ALL и ARCHIVED).
2. Батчами по BATCH_SIZE: /v3/product/info/list — карточка (название, SKU, штрихкод,
   категория, НДС), /v5/product/info/prices — цены и комиссии (пагинация по cursor).
3. Upsert товаров по (user_id, ozon_product_id); снепшот цены — только если поля
   цены отличаются от последнего сохранённого.

Ответы Ozon разбираются «мягко»: отсутствующее поле → None, а не исключение.
Поле `total` не используется (Ozon отключает его 23.11.2026), счётчики не нужны вовсе.
Форматы сверены с документацией Ozon (docs/ozon/, октябрь 2026) — см. docs/OZON_API.md.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.services.sync.parsing import list_items as _items
from app.services.sync.parsing import to_decimal as _dec
from app.services.sync.parsing import to_id as _int
from app.services.sync.parsing import to_str as _str
from app.models.product import Product, ProductPrice

log = logging.getLogger(__name__)

PRODUCT_LIST_PATH = "/v3/product/list"
PRODUCT_INFO_PATH = "/v3/product/info/list"
PRODUCT_PRICES_PATH = "/v5/product/info/prices"

LIST_LIMIT = 1000  # максимум Ozon для /v3/product/list
BATCH_SIZE = 1000  # максимум Ozon для info/list и prices
MAX_PAGES = 1000  # защита от бесконечной пагинации
VISIBILITIES = ("ALL", "ARCHIVED")  # по документации ALL — «все товары, кроме архивных»

_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")
_VAT_Q = Decimal("0.0001")

# Поля снепшота цены, по которым определяется «изменилось»
PRICE_FIELDS = (
    "price", "min_price", "old_price", "marketing_price",
    "commission_pct", "acquiring", "auto_action_enabled", "price_index",
)
PRODUCT_FIELDS = ("ozon_sku", "offer_id", "name", "category_id", "barcode", "vat", "is_archived")


@dataclass
class ProductSyncStats:
    created: int = 0  # новых товаров
    updated: int = 0  # изменилась карточка или цена
    unchanged: int = 0
    price_snapshots: int = 0  # сохранено новых снепшотов цены
    gone: int = 0  # товара больше нет в кабинете — перенесён в архив (шаг 8.4b)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


# ── разбор значений ──
def _vat(value: Any) -> Decimal | None:
    """НДС у Ozon бывает "0.2", 0.2 и "20%" — приводим к доле 0.2."""
    if value is None:
        return None
    text = str(value).strip()
    percent = text.endswith("%")
    rate = _dec(text.rstrip("%").strip(), _VAT_Q)
    if rate is None:
        return None
    return (rate / 100).quantize(_VAT_Q) if percent or rate > 1 else rate


def _product_attrs(list_item: dict, info: dict | None) -> dict[str, Any]:
    info = info or {}
    sku = info.get("sku") or list_item.get("sku")
    if not sku:
        sku = next((s.get("sku") for s in info.get("sources") or [] if isinstance(s, dict) and s.get("sku")), None)
    barcodes = info.get("barcodes") or []
    barcode = barcodes[0] if barcodes else info.get("barcode")
    archived = info.get("is_archived", list_item.get("archived", False)) or info.get("is_autoarchived", False)
    return {
        "ozon_sku": _int(sku),
        "offer_id": _str(info.get("offer_id") or list_item.get("offer_id"), 100) or "",
        "name": _str(info.get("name"), 500),
        "category_id": _int(info.get("description_category_id") or info.get("category_id")),
        "barcode": _str(barcode, 100),
        "vat": _vat(info.get("vat")),
        "is_archived": bool(archived),
    }


def price_attrs(item: dict) -> dict[str, Any]:
    price = item.get("price") if isinstance(item.get("price"), dict) else {}
    commissions = item.get("commissions") if isinstance(item.get("commissions"), dict) else {}
    indexes = item.get("price_indexes") if isinstance(item.get("price_indexes"), dict) else {}
    auto = price.get("auto_action_enabled")
    return {
        "price": _dec(price.get("price"), _CENT),
        "min_price": _dec(price.get("min_price"), _CENT),
        "old_price": _dec(price.get("old_price"), _CENT),
        # price.marketing_price Ozon удалил 12.11.2025; описание marketing_seller_price в выгрузке документации свёрнуто
        "marketing_price": _dec(price.get("marketing_seller_price"), _CENT),
        "commission_pct": _dec(commissions.get("sales_percent_fbo"), _MILLI),  # магазин работает по FBO
        "acquiring": _dec(item.get("acquiring"), _MILLI),  # ₽, не процент: у реальных товаров 19,8 при цене ~1300
        "auto_action_enabled": auto if isinstance(auto, bool) else None,
        "price_index": _str(indexes.get("color_index"), 32),
    }


def changed_snapshot(
    product_id: uuid.UUID, last: ProductPrice | None, attrs: dict[str, Any], now: datetime
) -> ProductPrice | None:
    """Новый снепшот цены, если поля отличаются от последнего сохранённого; иначе None (правило 12: изменение —
    новая запись, без изменений — без записи)."""
    if last is not None and all(_same(getattr(last, f), attrs[f]) for f in PRICE_FIELDS):
        return None
    return ProductPrice(product_id=product_id, snapshot_at=now, **attrs)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return (None if a is None else Decimal(str(a))) == (None if b is None else Decimal(str(b)))
    return a == b


# ── запросы к Ozon ──
async def fetch_product_ids(client: OzonClient) -> dict[int, dict]:
    """product_id → элемент списка (offer_id, archived …)."""
    found: dict[int, dict] = {}
    for visibility in VISIBILITIES:
        last_id = ""
        for _ in range(MAX_PAGES):
            data = await client.request(
                "POST", PRODUCT_LIST_PATH,
                {"filter": {"visibility": visibility}, "last_id": last_id, "limit": LIST_LIMIT},
            )
            result = (data.get("result") or {}) if isinstance(data, dict) else {}
            page = [i for i in result.get("items") or [] if isinstance(i, dict)]
            for item in page:
                pid = _int(item.get("product_id"))
                if pid:
                    if visibility == "ARCHIVED":
                        item = {**item, "archived": True}
                    found.setdefault(pid, item)
            last_id = result.get("last_id") or ""
            if not page or not last_id or len(page) < LIST_LIMIT:
                break
    return found


async def fetch_info(client: OzonClient, ids: list[int]) -> dict[int, dict]:
    # product_id в запросе — массив строк int64 (по схеме Ozon)
    data = await client.request("POST", PRODUCT_INFO_PATH, {"product_id": [str(i) for i in ids]})
    return {pid: i for i in _items(data) if (pid := _int(i.get("id") or i.get("product_id")))}


async def fetch_prices(client: OzonClient, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    cursor = ""
    for _ in range(MAX_PAGES):
        data = await client.request(
            "POST", PRODUCT_PRICES_PATH,
            {"cursor": cursor, "filter": {"product_id": [str(i) for i in ids], "visibility": "ALL"}, "limit": BATCH_SIZE},
        )
        page = _items(data)
        for item in page:
            if pid := _int(item.get("product_id")):
                out[pid] = item
        cursor = (data.get("cursor") or "") if isinstance(data, dict) else ""
        if not page or not cursor:
            break
    return out


# ── запись в БД ──
async def latest_prices(db: AsyncSession, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProductPrice]:
    if not product_ids:
        return {}
    latest = (
        select(ProductPrice.product_id, func.max(ProductPrice.snapshot_at).label("mx"))
        .where(ProductPrice.product_id.in_(product_ids))
        .group_by(ProductPrice.product_id)
        .subquery()
    )
    res = await db.execute(
        select(ProductPrice).join(
            latest, and_(ProductPrice.product_id == latest.c.product_id, ProductPrice.snapshot_at == latest.c.mx)
        )
    )
    return {p.product_id: p for p in res.scalars()}


async def archive_gone(db: AsyncSession, user_id: uuid.UUID, listed: set[int]) -> int:
    """Товары, которых нет в кабинете Ozon магазина ни в одном статусе (включая архив Ozon), — в архив: не удаляем
    (цены, остатки, журнал — история), вернётся в кабинет — синхронизация снимет архив. Так уходят из списков и товары
    другого кабинета, если раньше ключи двух магазинов были в одном (до шага 8.4b). Пустой список из Ozon — не повод
    архивировать всё."""
    if not listed:
        return 0
    rows = (await db.scalars(select(Product).where(
        Product.user_id == user_id, Product.is_archived.is_(False), Product.ozon_product_id.not_in(listed),
    ))).all()
    for product in rows:
        product.is_archived = True
    await db.commit()
    return len(rows)


async def sync_products(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> ProductSyncStats:
    stats = ProductSyncStats()
    listed = await fetch_product_ids(client)
    ids = sorted(listed)
    log.info("Синхронизация товаров: пользователь %s, в Ozon %d товаров", user_id, len(ids))

    for start in range(0, len(ids), BATCH_SIZE):
        batch = ids[start:start + BATCH_SIZE]
        info = await fetch_info(client, batch)
        prices = await fetch_prices(client, batch)
        now = datetime.now(timezone.utc)

        res = await db.execute(
            select(Product).where(Product.user_id == user_id, Product.ozon_product_id.in_(batch))
        )
        existing = {p.ozon_product_id: p for p in res.scalars()}
        last_prices = await latest_prices(db, [p.id for p in existing.values()])

        for pid in batch:
            attrs = _product_attrs(listed[pid], info.get(pid))
            product = existing.get(pid)
            changed = False
            if product is None:
                product = Product(user_id=user_id, ozon_product_id=pid, synced_at=now, **attrs)
                db.add(product)
                await db.flush()  # нужен product.id для снепшота
                stats.created += 1
            else:
                for field in PRODUCT_FIELDS:
                    if not _same(getattr(product, field), attrs[field]):
                        setattr(product, field, attrs[field])
                        changed = True
                product.synced_at = now

            if pid in prices:
                last = last_prices.get(product.id)
                if (snap := changed_snapshot(product.id, last, price_attrs(prices[pid]), now)) is not None:
                    db.add(snap)
                    stats.price_snapshots += 1
                    changed = changed or last is not None

            if pid not in existing:
                continue
            if changed:
                stats.updated += 1
            else:
                stats.unchanged += 1

        await db.commit()

    stats.gone = await archive_gone(db, user_id, set(listed))
    log.info("Синхронизация товаров: пользователь %s → %s", user_id, stats.as_dict())
    return stats
