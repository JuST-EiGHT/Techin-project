"""Финансовые начисления из Ozon API (/v1/finance/accrual/*) в БД.

Сверено с документацией Ozon (docs/ozon/seller_api_2026-10.txt):
- /v1/finance/accrual/types   — справочник типов начислений (id, name, description);
- /v1/finance/accrual/by-day  — начисления за ОДИН день (date ≥ 2022-01-01), пагинация
  по last_id (живёт 15 минут, в следующих запросах передаётся тот же date);
- /v1/finance/accrual/postings — плоские начисления по номерам отправлений (1–200 за раз):
  accrual_date, type_id, sku, accrued.amount, quantity.
/v3/finance/transaction/list отключён (8.09.2026). Периода в новых методах нет, поэтому
период = цикл по дням, разбивка на месяцы не нужна.

Схема (согласована с владельцем): by-day даёт список записей дня. Записи с товарами
отправления (unit_number + posting/item_fees) → номер отправления, его строки берём
из /postings (привязка к товару — по SKU строки). Записи без товаров (подписка, услуги,
хранение…) пишем из by-day как есть. Если /postings не вернул отправление — строки
разбираем из by-day (item_fees) и помечаем raw_json.fallback.

Защита от дублей — замена по областям: `posting:<номер>` и `day:<дата>`; см. models/accrual.py.
Каждый день — своя транзакция: при сбое на 40-м дне из 90 первые 39 уже сохранены,
а get_missing_periods покажет, что догрузить.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.models.accrual import EXPENSE, INCOME, SOURCE_API, Accrual, AccrualDay, AccrualType
from app.models.product import Product
from app.services.pnl_stale import invalidate
from app.services.sync.parsing import to_decimal, to_id, to_str

log = logging.getLogger(__name__)

TYPES_PATH = "/v1/finance/accrual/types"
BY_DAY_PATH = "/v1/finance/accrual/by-day"
POSTINGS_PATH = "/v1/finance/accrual/postings"

POSTINGS_BATCH = 200  # максимум posting_numbers в /postings
BACKFILL_DAYS = 90  # «последние 90 дней» при подключении ключа
MAX_PERIOD_DAYS = 92  # ручная перезагрузка за раз
OZON_MIN_DATE = date(2022, 1, 1)  # «самая ранняя — 1 января 2022 года»
MAX_PAGES = 1000
MSK = timezone(timedelta(hours=3))  # даты начислений Ozon — по Москве (без перехода на летнее время)

_CENT = Decimal("0.01")


def today_msk() -> date:
    return datetime.now(MSK).date()


@dataclass
class FinanceSyncStats:
    days: int = 0
    entries: int = 0  # записей by-day
    postings: int = 0  # отправлений, загруженных через /postings
    postings_fallback: int = 0  # отправлений, которые /postings не вернул (разобраны из by-day)
    lines: int = 0  # сохранено строк начислений
    types: int = 0  # типов в справочнике после синхронизации
    date_from: str = ""  # какие дни загружали
    date_to: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── разбор ──
def _money(obj: Any) -> Decimal | None:
    """{"amount": "-12.50", "currency": "RUB"} → Decimal."""
    return to_decimal(obj.get("amount"), _CENT) if isinstance(obj, dict) else None


def _date(value: Any, default: date) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def _is_posting_entry(e: dict) -> bool:
    posting = e.get("posting") if isinstance(e.get("posting"), dict) else {}
    item_fees = e.get("item_fees") if isinstance(e.get("item_fees"), dict) else {}
    return bool(to_str(e.get("unit_number"), 64)) and bool(posting.get("products") or item_fees.get("fees"))


def _non_item_lines(e: dict, day: date) -> list[dict]:
    """Записи by-day без товаров: non_item_fee, container_fees, иначе total_amount."""
    lines = []
    fee = e.get("non_item_fee") if isinstance(e.get("non_item_fee"), dict) else None
    if fee and _money(fee.get("accrued")) is not None:
        lines.append({"type_id": to_id(fee.get("type_id")) or to_id(e.get("accrual_id")), "amount": _money(fee.get("accrued"))})
    container = e.get("container_fees") if isinstance(e.get("container_fees"), dict) else {}
    for f in container.get("fees") or []:
        if isinstance(f, dict) and _money(f.get("accrued")) is not None:
            lines.append({"type_id": to_id(f.get("type_id")), "amount": _money(f.get("accrued"))})
    if not lines and _money(e.get("total_amount")) is not None:
        lines.append({"type_id": to_id(e.get("accrual_id")), "amount": _money(e.get("total_amount"))})
    d = _date(e.get("date"), day)
    return [{**ln, "date": d, "sku": None, "quantity": None} for ln in lines if ln["type_id"]]


def _fallback_posting_lines(e: dict, day: date) -> list[dict]:
    """Отправление, которого нет в ответе /postings: строки из item_fees by-day."""
    d = _date(e.get("date"), day)
    lines = []
    item_fees = e.get("item_fees") if isinstance(e.get("item_fees"), dict) else {}
    for item in item_fees.get("fees") or []:
        if not isinstance(item, dict):
            continue
        sku = to_id(item.get("sku"))
        for f in item.get("fees") or []:
            if isinstance(f, dict) and to_id(f.get("type_id")) and _money(f.get("accrued")) is not None:
                lines.append({"type_id": to_id(f["type_id"]), "amount": _money(f["accrued"]), "sku": sku, "date": d, "quantity": None})
    if not lines and to_id(e.get("accrual_id")) and _money(e.get("total_amount")) is not None:
        lines.append({"type_id": to_id(e["accrual_id"]), "amount": _money(e["total_amount"]), "sku": None, "date": d, "quantity": None})
    return lines


def _posting_lines(accruals: list[dict], default_day: date) -> list[dict]:
    lines = []
    for a in accruals:
        if not isinstance(a, dict) or not to_id(a.get("type_id")) or _money(a.get("accrued")) is None:
            continue
        qty = a.get("quantity")
        lines.append({
            "type_id": to_id(a["type_id"]), "amount": _money(a["accrued"]), "sku": to_id(a.get("sku")),
            "date": _date(a.get("accrual_date"), default_day), "quantity": int(qty) if isinstance(qty, int) else None,
            "raw": a,
        })
    return lines


# ── запросы к Ozon ──
async def fetch_types(client: OzonClient) -> list[dict]:
    data = await client.request("POST", TYPES_PATH, {})
    items = data.get("accrual_types") if isinstance(data, dict) else None
    return [t for t in items or [] if isinstance(t, dict)]


async def fetch_day(client: OzonClient, day: date) -> list[dict]:
    entries: list[dict] = []
    last_id = ""
    for _ in range(MAX_PAGES):
        data = await client.request("POST", BY_DAY_PATH, {"date": day.isoformat(), "last_id": last_id})
        page = data.get("accruals") if isinstance(data, dict) else None
        entries.extend(e for e in page or [] if isinstance(e, dict))
        next_id = (data.get("last_id") or "") if isinstance(data, dict) else ""
        if not page or not next_id or next_id == last_id:
            break
        last_id = next_id
    return entries


async def fetch_postings(client: OzonClient, numbers: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for start in range(0, len(numbers), POSTINGS_BATCH):
        batch = numbers[start:start + POSTINGS_BATCH]
        data = await client.request("POST", POSTINGS_PATH, {"posting_numbers": batch})
        for p in (data.get("posting_accruals") if isinstance(data, dict) else None) or []:
            if isinstance(p, dict) and (num := to_str(p.get("posting_number"), 64)):
                out[num] = [a for a in p.get("accruals") or [] if isinstance(a, dict)]
    return out


# ── БД ──
async def sync_accrual_types(db: AsyncSession, client: OzonClient) -> int:
    existing = {t.id: t for t in (await db.execute(select(AccrualType))).scalars()}
    for t in await fetch_types(client):
        type_id = to_id(t.get("id"))
        if not type_id:
            continue
        name = to_str(t.get("name"), 255) or f"Начисление #{type_id}"
        description = to_str(t.get("description"), 10_000)
        row = existing.get(type_id)
        if row is None:
            existing[type_id] = AccrualType(id=type_id, name_ru=name, description=description)
            db.add(existing[type_id])
        elif (row.name_ru, row.description) != (name, description):
            row.name_ru, row.description = name, description
    await db.commit()
    return len(existing)


async def _ensure_types(db: AsyncSession, lines: list[dict]) -> None:
    """Типы, которых нет в справочнике, — заглушкой; категорию — по знаку первой ненулевой суммы."""
    ids = {ln["type_id"] for ln in lines}
    if not ids:
        return
    types = {t.id: t for t in (await db.execute(select(AccrualType).where(AccrualType.id.in_(ids)))).scalars()}
    for ln in lines:
        t = types.get(ln["type_id"])
        if t is None:
            t = types[ln["type_id"]] = AccrualType(id=ln["type_id"], name_ru=f"Начисление #{ln['type_id']}")
            db.add(t)
        if t.category is None and ln["amount"]:
            t.category = INCOME if ln["amount"] > 0 else EXPENSE
    await db.flush()


async def _replace_scope(
    db: AsyncSession, user_id: uuid.UUID, scope: str, lines: list[dict], *,
    posting_number: str | None, products_by_sku: dict[int, uuid.UUID], raw_extra: dict | None = None,
) -> int:
    await db.execute(delete(Accrual).where(
        Accrual.user_id == user_id, Accrual.source == SOURCE_API, Accrual.sync_scope == scope,
    ))
    for n, ln in enumerate(lines):
        raw = ln.get("raw") or {k: (str(v) if isinstance(v, (Decimal, date)) else v) for k, v in ln.items()}
        db.add(Accrual(
            user_id=user_id, product_id=products_by_sku.get(ln["sku"]) if ln["sku"] else None,
            posting_number=posting_number, sku=ln["sku"], accrual_type_id=ln["type_id"], accrual_date=ln["date"],
            amount=ln["amount"], quantity=ln["quantity"], source=SOURCE_API, sync_scope=scope, line_no=n,
            raw_json={**raw, **(raw_extra or {})},
        ))
    return len(lines)


async def _sync_day(
    db: AsyncSession, user_id: uuid.UUID, client: OzonClient, day: date,
    products_by_sku: dict[int, uuid.UUID], stats: FinanceSyncStats,
) -> None:
    entries = await fetch_day(client, day)
    posting_entries: dict[str, list[dict]] = {}
    day_lines: list[dict] = []
    for e in entries:
        if _is_posting_entry(e):
            posting_entries.setdefault(to_str(e["unit_number"], 64), []).append(e)
        else:
            day_lines.extend({**ln, "raw": e} for ln in _non_item_lines(e, day))

    postings = await fetch_postings(client, sorted(posting_entries))
    res = await db.execute(select(Accrual.posting_number).where(
        Accrual.user_id == user_id, Accrual.source == SOURCE_API,
        Accrual.sync_scope.in_([f"posting:{n}" for n in posting_entries]),
    ).distinct())
    already_loaded = set(res.scalars())
    all_lines = list(day_lines)
    scopes: list[tuple[str, list[dict], str | None, dict | None]] = [(f"day:{day.isoformat()}", day_lines, None, None)]
    for number, es in posting_entries.items():
        if number in postings:
            lines = _posting_lines(postings[number], day)
            scopes.append((f"posting:{number}", lines, number, None))
            stats.postings += 1
            # полные данные отправления пришли — строки, разобранные раньше из by-day, больше не нужны
            await db.execute(delete(Accrual).where(
                Accrual.user_id == user_id, Accrual.source == SOURCE_API,
                Accrual.sync_scope.like(f"posting-day:{number}:%"),
            ))
        elif number in already_loaded:
            continue  # отправление уже загружено через /postings — из by-day не дублируем
        else:
            lines = [{**ln, "raw": e} for e in es for ln in _fallback_posting_lines(e, day)]
            # отдельная область: строки из by-day не затирают уже загруженные из /postings
            scopes.append((f"posting-day:{number}:{day.isoformat()}", lines, number, {"fallback": "by-day"}))
            stats.postings_fallback += 1
        all_lines.extend(lines)

    await _ensure_types(db, all_lines)
    for scope, lines, number, extra in scopes:
        stats.lines += await _replace_scope(
            db, user_id, scope, lines, posting_number=number, products_by_sku=products_by_sku, raw_extra=extra
        )

    mark = await db.get(AccrualDay, (user_id, day))
    if mark is None:
        db.add(AccrualDay(user_id=user_id, day=day, synced_at=datetime.now(timezone.utc), entries=len(entries)))
    else:
        mark.synced_at, mark.entries = datetime.now(timezone.utc), len(entries)
    await invalidate(db, user_id, day, day, f"Начисления из Ozon API за {day:%d.%m.%Y}")
    await db.commit()
    stats.days += 1
    stats.entries += len(entries)


async def loaded_days(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> set[date]:
    res = await db.execute(
        select(AccrualDay.day).where(AccrualDay.user_id == user_id, AccrualDay.day >= date_from, AccrualDay.day <= date_to)
    )
    return set(res.scalars())


async def get_missing_periods(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> list[tuple[date, date]]:
    """Непрерывные периоды [начало, конец], за которые начисления из API ещё не загружены."""
    have = await loaded_days(db, user_id, date_from, date_to)
    periods: list[tuple[date, date]] = []
    day = date_from
    while day <= date_to:
        if day not in have:
            start = day
            while day + timedelta(days=1) <= date_to and day + timedelta(days=1) not in have:
                day += timedelta(days=1)
            periods.append((start, day))
        day += timedelta(days=1)
    return periods


async def days_to_sync(
    db: AsyncSession, user_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None
) -> list[date]:
    """Без периода: вчера и сегодня всегда (данные за них ещё меняются) + незагруженные дни
    за последние BACKFILL_DAYS (при подключении ключа это и есть загрузка 90 дней).
    С периодом: все дни периода заново."""
    if date_from and date_to:
        return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    today = today_msk()
    start = max(today - timedelta(days=BACKFILL_DAYS - 1), OZON_MIN_DATE)
    days = {today - timedelta(days=1), today}
    for first, last in await get_missing_periods(db, user_id, start, today):
        days.update(first + timedelta(days=i) for i in range((last - first).days + 1))
    return sorted(days)


async def sync_finance(
    db: AsyncSession, user_id: uuid.UUID, client: OzonClient, date_from: date | None = None, date_to: date | None = None,
) -> FinanceSyncStats:
    stats = FinanceSyncStats()
    stats.types = await sync_accrual_types(db, client)
    res = await db.execute(select(Product.ozon_sku, Product.id).where(Product.user_id == user_id, Product.ozon_sku.is_not(None)))
    products_by_sku = dict(res.tuples().all())

    days = await days_to_sync(db, user_id, date_from, date_to)
    stats.date_from, stats.date_to = days[0].isoformat(), days[-1].isoformat()
    log.info("Начисления: пользователь %s, дней к загрузке %d (%s … %s)", user_id, len(days), days[0], days[-1])
    for day in days:
        await _sync_day(db, user_id, client, day, products_by_sku, stats)
    log.info("Начисления: пользователь %s → %s", user_id, stats.as_dict())
    return stats

