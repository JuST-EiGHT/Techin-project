"""Заявки покупателей на скидку из Ozon (шаг 4.3a) — вид синхронизации `discounts`, раз в 30 минут и кнопкой «Обновить».

Метод — `/v2/actions/discounts-task/list` (бета; v1 Ozon отключит — решение владельца 17.11.2026 брать сразу v2;
OZON_API.md «Заявки на скидку»): все статусы (`ALL`), по 50, следующая страница — `last_id` = id последней заявки, пока
страница полная.

Правило 12: заявка — строка навсегда; её последний статус и решение — для списка, каждая смена статуса или решения
(одобренная цена, количество, когда промодерирована) — новая запись `discount_task_events`. Пропала из ответа —
не трогаем. Имя и e-mail сотрудника продавца, который обработал заявку, не храним.

После загрузки — проверка «заявки на скидку» (аномалии `discount_new` / `discount_expiring`): новая заявка и «скоро
истекает» сразу попадают в колокольчик, «Требует внимания» и Telegram, не дожидаясь часовой проверки аномалий.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.models.discount import FROM_OZON, DiscountTask, DiscountTaskEvent
from app.models.product import Product
from app.services.sync.parsing import to_count, to_decimal, to_id, to_str

log = logging.getLogger(__name__)

LIST_PATH = "/v2/actions/discounts-task/list"
PAGE = 50  # максимум limit (5, 10, 15, 20, 30, 50)
MAX_PAGES = 200  # 10 000 заявок — защита от бесконечного цикла

# данные сотрудника продавца, обработавшего заявку, и имя покупателя (было в v1) — персональные: в сырой записи не храним
PERSONAL = ("email", "first_name", "last_name", "patronymic", "customer_name")
_KOP = Decimal("0.01")


@dataclass
class DiscountSyncStats:
    tasks: int = 0
    new: int = 0
    status_changes: int = 0
    unknown_products: int = 0
    pages: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Parsed:
    ozon_task_id: int
    sku: int | None
    name: str | None
    status: str
    created_at: datetime | None
    end_at: datetime | None
    edited_till: datetime | None
    requested_price: Decimal | None
    requested_discount_pct: Decimal | None
    requested_quantity_max: int | None
    original_price: Decimal | None
    min_auto_price: Decimal | None
    reduction_factor: Decimal | None
    approved_price: Decimal | None
    approved_discount: Decimal | None
    approved_quantity_max: int | None
    moderated_at: datetime | None
    is_auto_moderated: bool | None
    auto_moderated_info: dict | None
    raw: dict


def _dt(value) -> datetime | None:
    """Дата Ozon (ISO 8601, `Z` или смещение); «0001-01-01», пусто и мусор — нет даты."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.year < 2000:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _money(value) -> Decimal | None:
    """Цена: 0 и пусто — нет значения (у неодобренной заявки Ozon отдаёт 0)."""
    d = to_decimal(value, _KOP)
    return d if d is not None and d > 0 else None


def _qty(value) -> int | None:
    return to_count(value) or None


def clean(raw: dict) -> dict:
    """Сырая запись без персональных данных."""
    return {k: v for k, v in raw.items() if k not in PERSONAL}


def parse(raw: dict) -> Parsed | None:
    """Заявка из ответа Ozon; без id — None. Статус вне перечня — как прислал Ozon (заглавными, до 16 знаков)."""
    task_id = to_id(raw.get("id"))
    if task_id is None:
        return None
    info = raw.get("auto_moderated_info")
    auto = raw.get("is_auto_moderated")
    return Parsed(
        ozon_task_id=task_id, sku=to_id(raw.get("sku")), name=to_str(raw.get("name"), 500),
        status=(to_str(raw.get("status"), 16) or "NEW").upper(),
        created_at=_dt(raw.get("created_at")), end_at=_dt(raw.get("end_at")), edited_till=_dt(raw.get("edited_till")),
        requested_price=_money(raw.get("requested_price")),
        requested_discount_pct=to_decimal(raw.get("requested_discount"), Decimal("0.01")),
        requested_quantity_max=_qty(raw.get("requested_quantity_max")),
        original_price=_money(raw.get("original_price")), min_auto_price=_money(raw.get("min_auto_price")),
        reduction_factor=to_decimal(raw.get("reduction_factor"), Decimal("0.0001")),
        approved_price=_money(raw.get("approved_price")), approved_discount=_money(raw.get("approved_discount")),
        approved_quantity_max=_qty(raw.get("approved_quantity_max")), moderated_at=_dt(raw.get("moderated_at")),
        is_auto_moderated=auto if isinstance(auto, bool) else None,
        auto_moderated_info=info if isinstance(info, dict) and any(info.values()) else None,
        raw=clean(raw),
    )


async def fetch(client: OzonClient) -> list[dict]:
    """Все заявки магазина (все статусы), постранично."""
    out: list[dict] = []
    last_id: int | None = None
    for _ in range(MAX_PAGES):
        body: dict = {"limit": PAGE, "status": "ALL"}
        if last_id is not None:
            body["last_id"] = last_id
        data = await client.request("POST", LIST_PATH, body) or {}
        page = [t for t in data.get("tasks") or [] if isinstance(t, dict)]
        out.extend(page)
        nxt = to_id(page[-1].get("id")) if page else None
        if len(page) < PAGE or nxt is None or nxt == last_id:
            break
        last_id = nxt
    return out


def _utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decision_of(status: str, price, qty, moderated_at, auto) -> tuple:
    """Что считаем «сменой» заявки: статус, одобренная цена и количество, когда и как промодерирована."""
    return (status, None if price is None else Decimal(price).quantize(_KOP), qty, _utc(moderated_at), auto)


async def latest_events(db: AsyncSession, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, DiscountTaskEvent]:
    if not task_ids:
        return {}
    rows = (await db.execute(
        select(DiscountTaskEvent).where(DiscountTaskEvent.task_id.in_(task_ids))
        .order_by(DiscountTaskEvent.task_id, DiscountTaskEvent.seen_at)
    )).scalars()
    return {e.task_id: e for e in rows}  # по возрастанию — остаётся последнее


def _apply(task: DiscountTask, p: Parsed, now: datetime) -> None:
    task.sku, task.name, task.status = p.sku, p.name or task.name, p.status
    task.created_at_ozon, task.end_at, task.edited_till = p.created_at, p.end_at, p.edited_till
    task.requested_price, task.requested_discount_pct = p.requested_price, p.requested_discount_pct
    task.requested_quantity_max, task.original_price = p.requested_quantity_max, p.original_price
    task.min_auto_price, task.reduction_factor = p.min_auto_price, p.reduction_factor
    task.approved_price, task.approved_discount = p.approved_price, p.approved_discount
    task.approved_quantity_max, task.moderated_at = p.approved_quantity_max, p.moderated_at
    task.is_auto_moderated, task.auto_moderated_info = p.is_auto_moderated, p.auto_moderated_info
    task.raw_json, task.synced_at = p.raw, now


async def sync_discount_tasks(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> DiscountSyncStats:
    stats = DiscountSyncStats()
    now = datetime.now(timezone.utc)
    raws = await fetch(client)
    parsed = [p for r in raws if (p := parse(r)) is not None]
    stats.tasks = len(parsed)
    existing = {t.ozon_task_id: t for t in (await db.execute(
        select(DiscountTask).where(DiscountTask.user_id == user_id)
    )).scalars()}
    products = {p.ozon_sku: p.id for p in (await db.execute(
        select(Product).where(Product.user_id == user_id)
    )).scalars() if p.ozon_sku}
    last = await latest_events(db, [t.id for t in existing.values()])
    for p in parsed:
        task = existing.get(p.ozon_task_id)
        if task is None:
            task = DiscountTask(id=uuid.uuid4(), user_id=user_id, ozon_task_id=p.ozon_task_id, first_seen_at=now,
                                status=p.status, synced_at=now)
            db.add(task)
            existing[p.ozon_task_id] = task
            stats.new += 1
        task.product_id = products.get(p.sku) if p.sku else None
        if task.product_id is None:
            stats.unknown_products += 1
        _apply(task, p, now)
        current = decision_of(p.status, p.approved_price, p.approved_quantity_max, p.moderated_at, p.is_auto_moderated)
        prev = last.get(task.id)
        if prev is None or decision_of(prev.status, prev.approved_price, prev.approved_quantity_max,
                                       prev.moderated_at, prev.is_auto_moderated) != current:
            event = DiscountTaskEvent(task_id=task.id, status=p.status, approved_price=p.approved_price,
                                      approved_quantity_max=p.approved_quantity_max, moderated_at=p.moderated_at,
                                      is_auto_moderated=p.is_auto_moderated, source=FROM_OZON, seen_at=now)
            db.add(event)
            last[task.id] = event
            if prev is not None:
                stats.status_changes += 1
    await db.commit()
    log.info("Заявки на скидку: пользователь %s → заявок %d, новых %d, изменилось %d",
             user_id, stats.tasks, stats.new, stats.status_changes)
    return stats
