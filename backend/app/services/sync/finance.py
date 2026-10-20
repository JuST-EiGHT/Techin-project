"""Финансовые начисления из Ozon API (/v1/finance/accrual/*) в БД.

Сверено с документацией Ozon (docs/ozon/seller_api_2026-10.txt) и с ответами Ozon на реальном магазине (20.10.2026):
- /v1/finance/accrual/types   — справочник: `name` — английский код (SaleCommission, Logistic…), `description` —
  русское название («Вознаграждение за продажу», «Логистика»); его и показываем;
- /v1/finance/accrual/by-day  — все начисления за ОДИН день (date ≥ 2022-01-01), пагинация по last_id (живёт 15 минут,
  в следующих запросах — тот же date). Виды записей (accrued_category):
  POSTING — продажа или доставка по отправлению: posting.products[] с commission (sale_amount, bonus, coinvestment,
  commission…) и delivery.services[] (логистика, доставка до места выдачи…);
  ITEM — начисления по товару: item_fees.fees[].fees[] (эквайринг, «Звёздные товары», вывоз со склада…);
  NON_ITEM — начисления без товара: non_item_fee (размещение, кросс-докинг, продвижение…); container_fees — по грузоместам.
/v3/finance/transaction/list отключён (8.09.2026). Периода в новых методах нет — период = цикл по дням.

Выручки как типа в справочнике нет: она — в записи отправления. Сверено с отчётом XLSX по начислениям на трёх
отправлениях владельца копейка в копейку: «Выручка» = sale_amount − bonus − coinvestment (= sale_price при 1 шт.),
«Баллы за скидки» = bonus, «Программы партнёров» = coinvestment, «Вознаграждение за продажу» = commission; сумма строк
записи = total_amount. Эти суммы пишем служебными типами с отрицательным id (VIRTUAL_TYPES) — так их видят P&L, прибыль
по товарам, продажи по дням и список начислений. Отрицательная выручка — «Возврат выручки» (по реальному возврату ещё
не сверено — шаг 8.4a).

/v1/finance/accrual/postings не используем: начисления по отправлению в нём неполные (нет эквайринга), номера —
только вида 123-456-7 (у части записей by-day unit_number другого вида — Ozon отвечал 400 и загрузка падала), а лимит
запросов в секунду у метода маленький (429). Всё нужное есть в by-day.

Защита от дублей — замена области `day:<дата>` целиком; см. models/accrual.py. Каждый день — своя транзакция: при
сбое на 40-м дне из 90 первые 39 уже сохранены, а get_missing_periods покажет, что догрузить.
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

BACKFILL_DAYS = 90  # «последние 90 дней» при подключении ключа
MAX_PERIOD_DAYS = 92  # ручная перезагрузка за раз
OZON_MIN_DATE = date(2022, 1, 1)  # «самая ранняя — 1 января 2022 года»
MAX_PAGES = 1000
MSK = timezone(timedelta(hours=3))  # даты начислений Ozon — по Москве (без перехода на летнее время)

# Суммы продажи из записи отправления — служебные типы (в справочнике Ozon их нет). Названия — как в отчёте XLSX.
REVENUE, RETURN, BONUS, PARTNERS, COMMISSION = -1, -2, -3, -4, -5
VIRTUAL_TYPES = {
    REVENUE: ("SaleRevenue", "Выручка", INCOME),
    RETURN: ("SaleRevenueReturn", "Возврат выручки", EXPENSE),
    BONUS: ("DiscountPoints", "Баллы за скидки", INCOME),
    PARTNERS: ("PartnerPrograms", "Программы партнёров", INCOME),
    COMMISSION: ("SaleCommissionFromPosting", "Вознаграждение за продажу", EXPENSE),
}

_CENT = Decimal("0.01")


def today_msk() -> date:
    return datetime.now(MSK).date()


@dataclass
class FinanceSyncStats:
    days: int = 0
    entries: int = 0  # записей by-day
    postings: int = 0  # записей с отправлением (продажи, доставка, возвраты)
    unbalanced: int = 0  # записей, где сумма разобранных строк не сошлась с total_amount Ozon (формат изменился?)
    lines: int = 0  # сохранено строк начислений
    types: int = 0  # типов в справочнике после синхронизации
    date_from: str = ""  # какие дни загружали
    date_to: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── разбор ──
def _money(obj: Any) -> Decimal | None:
    """{"amount": "-12.50", "currency": "RUB"} → Decimal; нет суммы — None."""
    return to_decimal(obj.get("amount"), _CENT) if isinstance(obj, dict) else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _date(value: Any, default: date) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def _qty(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_entry(e: dict, day: date) -> list[dict]:
    """Запись by-day → строки начислений: {type_id, amount, sku, quantity, date, posting}."""
    d = _date(e.get("date"), day)
    unit = to_str(e.get("unit_number"), 64)  # «ID начисления», как в отчёте XLSX: номер отправления, заказа, поставки…
    lines: list[dict] = []

    def add(type_id: int | None, amount: Decimal | None, sku: int | None = None, qty: int | None = None) -> None:
        if type_id and amount is not None:
            lines.append({"type_id": type_id, "amount": amount, "sku": sku, "quantity": qty, "date": d, "posting": unit})

    for product in _dict(e.get("posting")).get("products") or []:
        if not isinstance(product, dict):
            continue
        sku, qty = to_id(product.get("sku")), _qty(product.get("quantity"))
        c = _dict(product.get("commission"))
        sale, bonus, partners = _money(c.get("sale_amount")), _money(c.get("bonus")), _money(c.get("coinvestment"))
        commission = _money(c.get("commission"))
        if commission is None:
            commission = _money(c.get("sale_commission"))
        if sale:
            revenue = sale - (bonus or 0) - (partners or 0)
            add(REVENUE if revenue >= 0 else RETURN, revenue, sku, qty)
        if bonus:
            add(BONUS, bonus, sku, qty)
        if partners:
            add(PARTNERS, partners, sku, qty)
        if commission:
            add(COMMISSION, commission, sku, qty)
        for service in _dict(product.get("delivery")).get("services") or []:
            if isinstance(service, dict):
                add(to_id(service.get("type_id")), _money(service.get("accrued")), sku, qty)
    for item in _dict(e.get("item_fees")).get("fees") or []:
        if not isinstance(item, dict):
            continue
        sku = to_id(item.get("sku"))
        for fee in item.get("fees") or []:
            if isinstance(fee, dict):
                add(to_id(fee.get("type_id")), _money(fee.get("accrued")), sku)
    fee = _dict(e.get("non_item_fee"))
    if fee:
        add(to_id(fee.get("type_id")) or to_id(e.get("accrual_id")), _money(fee.get("accrued")))
    for fee in _dict(e.get("container_fees")).get("fees") or []:
        if isinstance(fee, dict):
            add(to_id(fee.get("type_id")), _money(fee.get("accrued")))
    if not lines:  # разбирать нечего — итог записи, чтобы сумма дня сошлась с Ozon
        add(to_id(e.get("accrual_id")), _money(e.get("total_amount")))
    return lines


def is_balanced(e: dict, lines: list[dict]) -> bool:
    """Сумма разобранных строк = total_amount записи (нет итога — проверять не с чем)."""
    total = _money(e.get("total_amount"))
    return total is None or abs(sum((ln["amount"] for ln in lines), Decimal(0)) - total) <= _CENT


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


# ── БД ──
async def sync_accrual_types(db: AsyncSession, client: OzonClient) -> int:
    """Справочник Ozon и служебные типы. Русское название — description (в name Ozon отдаёт английский код)."""
    existing = {t.id: t for t in (await db.execute(select(AccrualType))).scalars()}
    wanted: dict[int, tuple[str | None, str, str | None]] = {}
    for t in await fetch_types(client):
        type_id = to_id(t.get("id"))
        if not type_id:
            continue
        code = to_str(t.get("name"), 100)
        description = to_str(t.get("description"), 10_000)
        wanted[type_id] = (code, to_str(description, 255) or code or f"Начисление #{type_id}", description)
    for type_id, (code, name, _category) in VIRTUAL_TYPES.items():
        wanted[type_id] = (code, name, None)
    for type_id, (code, name, description) in wanted.items():
        row = existing.get(type_id)
        if row is None:
            existing[type_id] = row = AccrualType(id=type_id, code=code, name_ru=name, description=description)
            db.add(row)
        elif (row.code, row.name_ru, row.description) != (code, name, description):
            row.code, row.name_ru, row.description = code, name, description
        if type_id in VIRTUAL_TYPES:
            row.category = VIRTUAL_TYPES[type_id][2]
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
    db: AsyncSession, user_id: uuid.UUID, scope: str, lines: list[dict], products_by_sku: dict[int, uuid.UUID],
) -> int:
    await db.execute(delete(Accrual).where(
        Accrual.user_id == user_id, Accrual.source == SOURCE_API, Accrual.sync_scope == scope,
    ))
    for n, ln in enumerate(lines):
        db.add(Accrual(
            user_id=user_id, product_id=products_by_sku.get(ln["sku"]) if ln["sku"] else None,
            posting_number=ln["posting"], sku=ln["sku"], accrual_type_id=ln["type_id"], accrual_date=ln["date"],
            amount=ln["amount"], quantity=ln["quantity"], source=SOURCE_API, sync_scope=scope, line_no=n,
            raw_json=ln["raw"],
        ))
    return len(lines)


async def _sync_day(
    db: AsyncSession, user_id: uuid.UUID, client: OzonClient, day: date,
    products_by_sku: dict[int, uuid.UUID], stats: FinanceSyncStats,
) -> None:
    entries = await fetch_day(client, day)
    lines: list[dict] = []
    for e in entries:
        parsed = parse_entry(e, day)
        if _dict(e.get("posting")).get("products"):
            stats.postings += 1
        if not is_balanced(e, parsed):
            stats.unbalanced += 1
            log.warning("Начисления %s: сумма строк записи не сошлась с total_amount Ozon (вид %s)",
                        day, e.get("accrued_category"))
        lines.extend({**ln, "raw": e} for ln in parsed)  # исходная запись Ozon — для перепроверки и перечитывания

    await _ensure_types(db, lines)
    stats.lines += await _replace_scope(db, user_id, f"day:{day.isoformat()}", lines, products_by_sku)

    mark = await db.get(AccrualDay, (user_id, day))
    if mark is None:
        db.add(AccrualDay(user_id=user_id, day=day, synced_at=datetime.now(timezone.utc), entries=len(entries)))
    else:
        mark.synced_at, mark.entries = datetime.now(timezone.utc), len(entries)
    await invalidate(db, user_id, day, day, f"Начисления из Ozon API за {day:%d.%m.%Y}")
    from app.services.sales import rebuild as rebuild_sales  # sales → costs → этот модуль: импорт здесь, без цикла

    await rebuild_sales(db, user_id, day, day)
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

