"""Синхронизация акций Ozon в БД.

Сверено с документацией Ozon (docs/ozon/seller_api_2026-10.txt):
- GET  /v1/actions — акции, в которых можно участвовать (тип, даты, freeze_date,
  auto_add_dates, is_participating);
- POST /v2/actions/products   — товары в акции (last_id, limit ≤ 100);
- POST /v2/actions/candidates — товары, которые могут участвовать (last_id, limit ≤ 100);
- POST /v2/actions/auto-add/products/list — товары автодобавления на дату из auto_add_dates
  (offset, limit 1–100).
/v1/actions/products и /v1/actions/candidates Ozon отключает 13.10.2026 — не используются.

«Добавлен автоматически» (без действия пользователя):
- Ozon отдаёт у товара в акции add_mode; перечня значений в документации нет, в примерах —
  SELLER, MANUAL, NOT_SET. SELLER/MANUAL — добавил продавец; значение с AUTO — автоматически;
  NOT_SET и неизвестные — решают другие признаки;
- товар есть в списке автодобавления на уже наступившую дату автодобавления;
- уже был отмечен автоматическим и Ozon не сообщил обратного.
Сырое add_mode хранится — правило можно уточнить на реальных данных.

Цены у Ozon в v2 — объекты {"amount": "1800", "currency": "RUB"}, в v1 — числа; разбираем оба.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.price_calculator import margin_at
from app.engine.promotions import is_whole_stock
from app.integrations.ozon.client import OzonClient
from app.models.product import Product
from app.models.promotion import Promotion, PromotionPriceSnapshot, PromotionProduct
from app.schemas.sync import utc
from app.services import pricing
from app.services.sync.parsing import to_count, to_decimal, to_id, to_str

log = logging.getLogger(__name__)

ACTIONS_PATH = "/v1/actions"
PRODUCTS_PATH = "/v2/actions/products"
CANDIDATES_PATH = "/v2/actions/candidates"
AUTO_ADD_PATH = "/v2/actions/auto-add/products/list"

PAGE = 100  # максимум limit во всех методах акций
MAX_PAGES = 1000
SELLER_MODES = {"SELLER", "MANUAL"}
UNKNOWN_MODES = {"", "NOT_SET", "UNKNOWN", "UNSPECIFIED"}

_CENT = Decimal("0.01")


@dataclass
class PromotionSyncStats:
    actions: int = 0
    participating_actions: int = 0
    whole_stock_actions: int = 0
    products_active: int = 0  # товаров в акциях (участвуют)
    candidates: int = 0
    auto_added: int = 0
    entered: int = 0  # вошли в акцию с прошлой синхронизации
    left: int = 0  # вышли (строка участия закрыта датой выхода)
    price_changes: int = 0  # у участника сменилась цена или количество в акции
    unknown_products: int = 0  # товары Ozon, которых нет в БД (нужна синхронизация товаров)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


# ── разбор ──
def money(value: Any) -> Decimal | None:
    """{"amount": "1800", "currency": "RUB"} (v2) или число (v1) → Decimal."""
    if isinstance(value, dict):
        value = value.get("amount")
    return to_decimal(value, _CENT)


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def auto_by_mode(mode: str | None) -> bool | None:
    """True — Ozon добавил сам, False — продавец, None — неизвестно."""
    m = (mode or "").strip().upper()
    if m in UNKNOWN_MODES:
        return None
    if m in SELLER_MODES:
        return False
    return True if "AUTO" in m else None


def _page(data: Any) -> tuple[list[dict], str]:
    if not isinstance(data, dict):
        return [], ""
    body = data.get("result") if isinstance(data.get("result"), dict) else data
    items = [p for p in body.get("products") or [] if isinstance(p, dict)]
    last_id = body.get("last_id") or data.get("last_id") or ""
    return items, str(last_id)


# ── запросы к Ozon ──
async def fetch_actions(client: OzonClient) -> list[dict]:
    data = await client.request("GET", ACTIONS_PATH)
    items = data.get("result") if isinstance(data, dict) else None
    return [a for a in items or [] if isinstance(a, dict)]


async def fetch_action_products(client: OzonClient, path: str, action_id: int) -> list[dict]:
    out: list[dict] = []
    last_id = ""
    for _ in range(MAX_PAGES):
        data = await client.request("POST", path, {"action_id": action_id, "last_id": last_id, "limit": PAGE})
        items, next_id = _page(data)
        out.extend(items)
        if not items or not next_id or next_id == last_id or len(items) < PAGE:
            break
        last_id = next_id
    return out


async def fetch_auto_added(client: OzonClient, action_id: int, dates: list[str], now: datetime) -> set[int]:
    """product_id товаров из автодобавления на уже наступившие даты."""
    ids: set[int] = set()
    for raw in dates:
        when = parse_dt(raw)
        if when is None or when > now:
            continue
        offset = 0
        for _ in range(MAX_PAGES):
            data = await client.request(
                "POST", AUTO_ADD_PATH, {"action_id": action_id, "auto_add_date": raw, "offset": offset, "limit": PAGE}
            )
            items, _ = _page(data)
            ids.update(pid for p in items if (pid := to_id(p.get("product_id"))))
            if len(items) < PAGE:
                break
            offset += PAGE
    return ids


# ── БД ──
def _promotion_attrs(a: dict) -> dict[str, Any]:
    title = to_str(a.get("title"), 500) or f"Акция #{to_id(a.get('id'))}"
    action_type = to_str(a.get("action_type"), 64)
    dates = [d for d in a.get("auto_add_dates") or [] if isinstance(d, str)]
    return {
        "title": title,
        "action_type": action_type,
        "description": to_str(a.get("description"), 10_000),
        "date_from": parse_dt(a.get("date_start")),
        "date_to": parse_dt(a.get("date_end")),
        "discount_type": to_str(a.get("discount_type"), 32),
        "discount_value": to_decimal(a.get("discount_value"), _CENT),
        "freeze_date": parse_dt(a.get("freeze_date")),
        "auto_add_dates": dates or None,
        "is_participating": bool(a.get("is_participating")),
        "participating_products_count": to_count(a.get("participating_products_count")),
        "potential_products_count": to_count(a.get("potential_products_count")),
        "whole_stock": is_whole_stock(title, action_type),
    }


def _close(row: PromotionProduct, when: datetime) -> None:
    """Товар вышел из акции: строка остаётся с датой выхода (история участия)."""
    row.left_at = when


def snapshot(db: AsyncSession, row: PromotionProduct, when: datetime) -> None:
    """Цена и количество товара в акции на момент входа или изменения."""
    db.add(PromotionPriceSnapshot(promotion_product_id=row.id, snapshot_at=when, action_price=row.action_price, stock=row.stock))


async def current_rows(db: AsyncSession, promotion_id: uuid.UUID) -> list[PromotionProduct]:
    """Текущие строки акции: участие без даты выхода и кандидаты."""
    res = await db.execute(
        select(PromotionProduct).where(PromotionProduct.promotion_id == promotion_id, PromotionProduct.left_at.is_(None))
    )
    return list(res.scalars())


async def sync_promotions(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> PromotionSyncStats:
    stats = PromotionSyncStats()
    now = datetime.now(timezone.utc)

    products = {p.ozon_product_id: p for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()}
    # маржа в акции — калькулятор цены (шаг 4.2): себестоимость, комиссия, логистика, реклама, налог
    ctx = await pricing.load(db, user_id, list(products.values()))

    existing = {
        p.ozon_action_id: p
        for p in (await db.execute(select(Promotion).where(Promotion.user_id == user_id))).scalars()
    }
    seen_actions: set[int] = set()
    for a in await fetch_actions(client):
        action_id = to_id(a.get("id"))
        if not action_id:
            continue
        seen_actions.add(action_id)
        attrs = _promotion_attrs(a)
        promo = existing.get(action_id)
        if promo is None:
            promo = Promotion(user_id=user_id, ozon_action_id=action_id, synced_at=now, **attrs)
            db.add(promo)
            await db.flush()
        else:
            for field, value in attrs.items():
                setattr(promo, field, value)
            promo.synced_at = now
        stats.actions += 1
        stats.participating_actions += promo.is_participating
        stats.whole_stock_actions += promo.whole_stock

        active = await fetch_action_products(client, PRODUCTS_PATH, action_id)
        candidates = await fetch_action_products(client, CANDIDATES_PATH, action_id)
        auto_ids = await fetch_auto_added(client, action_id, promo.auto_add_dates or [], now) if active else set()

        rows = await current_rows(db, promo.id)
        joined = {r.product_id: r for r in rows if r.is_active}  # участвуют сейчас
        offered = {r.product_id: r for r in rows if not r.is_active}  # кандидаты

        def values(item: dict, product: Product, is_active: bool, auto: bool, mode: str | None) -> dict:
            last_price = ctx.prices.get(product.id)
            params = ctx.product_params(product)
            action_price = money(item.get("action_price"))
            return {
                "action_price": action_price,
                "price": money(item.get("price")),
                # 0 у Ozon — «не задана»: тогда мин. цена товара, а если и она 0 — пусто
                "min_price": money(item.get("min_seller_price")) or (last_price.min_price if last_price else None) or None,
                "max_action_price": money(item.get("max_action_price")),
                "stock": to_count(item.get("stock")) if item.get("stock") is not None else None,
                "is_active": is_active,
                "added_automatically": auto,
                "add_mode": mode,
                "margin_in_promo": margin_at(params, action_price) if params is not None else None,
                "synced_at": now,
            }

        # 1. участники: новый — вход (строка и снимок), уже участвует — изменение цены или количества — снимок
        seen_active: set[uuid.UUID] = set()
        for item in active:
            product = products.get(to_id(item.get("id") or item.get("product_id")))
            if product is None:
                stats.unknown_products += 1
                continue
            if product.id in seen_active:
                continue
            seen_active.add(product.id)
            row = joined.get(product.id)
            mode = to_str(item.get("add_mode"), 32)
            by_mode = auto_by_mode(mode)
            auto = (
                by_mode is True
                or (by_mode is None and product.ozon_product_id in auto_ids)
                or (by_mode is None and row is not None and row.added_automatically)
            )
            vals = values(item, product, True, auto, mode)
            if row is None:
                if (cand := offered.pop(product.id, None)) is not None:  # был кандидатом — теперь участник
                    await db.delete(cand)
                    await db.flush()
                row = PromotionProduct(promotion_id=promo.id, product_id=product.id, first_seen_at=now, **vals)
                db.add(row)
                await db.flush()
                snapshot(db, row, now)
                stats.entered += 1
            else:
                changed = row.action_price != vals["action_price"] or row.stock != vals["stock"]
                for field, value in vals.items():
                    setattr(row, field, value)
                if changed:
                    snapshot(db, row, now)
                    stats.price_changes += 1
            stats.products_active += 1
            stats.auto_added += auto

        # 2. участвовал, а среди участников больше нет — вышел: дата выхода, строка остаётся
        for product_id, row in joined.items():
            if product_id not in seen_active:
                _close(row, now)
                stats.left += 1
        await db.flush()

        # 3. кандидаты — текущее состояние Ozon: заменяем
        seen_offered: set[uuid.UUID] = set()
        for item in candidates:
            product = products.get(to_id(item.get("id") or item.get("product_id")))
            if product is None:
                stats.unknown_products += 1
                continue
            if product.id in seen_active or product.id in seen_offered:  # участвующий — не кандидат
                continue
            seen_offered.add(product.id)
            vals = values(item, product, False, False, to_str(item.get("add_mode"), 32))
            vals["min_stock"] = to_count(item.get("min_stock")) if item.get("min_stock") is not None else None
            vals["recommended_stock"] = (
                to_count(item.get("recommended_stock")) if item.get("recommended_stock") is not None else None
            )
            row = offered.get(product.id)
            if row is None:
                db.add(PromotionProduct(promotion_id=promo.id, product_id=product.id, first_seen_at=now, **vals))
            else:
                for field, value in vals.items():
                    setattr(row, field, value)
            stats.candidates += 1
        gone = [r.id for pid, r in offered.items() if pid not in seen_offered]
        if gone:
            await db.execute(delete(PromotionProduct).where(PromotionProduct.id.in_(gone)))

    # акции, которых Ozon больше не отдаёт (закончились или отменены), не удаляем: участие в них закрываем датой
    # окончания (или сейчас, если она ещё не наступила), кандидатов убираем
    for action_id, promo in existing.items():
        if action_id in seen_actions:
            continue
        date_to = utc(promo.date_to)
        end = date_to if date_to is not None and date_to < now else now
        for row in await current_rows(db, promo.id):
            if row.is_active:
                _close(row, end)
                stats.left += 1
            else:
                await db.delete(row)

    await db.commit()
    log.info("Синхронизация акций: пользователь %s → %s", user_id, stats.as_dict())
    return stats
