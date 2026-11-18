"""Заявки покупателей на скидку (шаг 4.3a): список с расчётом прибыли, решение по заявкам через Ozon.

Расчёт — калькулятор цены 4.2 (`services/pricing`): при запрошенной цене — маржа и прибыль с 1 шт., минимальная цена для
участия (прибыль 0) и целевая (ваша целевая маржа), остаток. Пометки и «одобрили бы по правилу» — `engine/discounts`.

Решение — правило 6, как цены (6.2–6.3) и вход в акции (4.3):
1. Предпросмотр (`preview`) — по одной заявке или списком: что уйдёт в Ozon, маржа, проверки. Записи
   `discount_decisions` со status preview и общим batch_id, живут 15 минут. В Ozon ничего не уходит.
2. Применение (`apply`) — только по batch_id: свой, не устарел, ещё не применялся; больше 10 заявок — подтверждение;
   цена в минус — только с «понимаю» (решение владельца 17.11.2026). Проверки — заново по свежему состоянию заявки
   (её могли решить в кабинете Ozon). Попытка — новая запись с preview_id (уникально: один предпросмотр — одна попытка),
   сначала со status unknown — если сервис упадёт посреди запроса, история честно скажет «могло примениться».
3. Ozon: одобрить — `/v1/actions/discounts-task/approve`, отклонить — `/decline`, без повторов (`retry=False`): повтор
   после таймаута Ozon отклонил бы («уже не новая»), и сервис показал бы неправду. Ответ — по каждой заявке
   (`fail_details[{task_id, error_for_user}]`): у каждой свой итог и свой текст.
4. Журнал (`audit_log`, entity_type discount_task, entity_id — id заявки Ozon): до и после, запрос и ответ Ozon, IP,
   User-Agent; принятое решение — новое событие заявки (`discount_task_events`, source service).

Жёсткие проверки одобрения (не отключаются): заявка новая и её срок не прошёл; цена > 0, не ниже запрошенной
покупателем и ниже вашей текущей цены; не ниже себестоимости с НДС × 1,05 (нет себестоимости — нельзя, правило 5); данные
Ozon не аномальны (есть цена, комиссия < 90 %); количество — от 1 до запрошенного покупателем. Предупреждения: ниже
минимальной цены в Ozon, маржа ниже порога, больше, чем на складах, скидка больше 70 %.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import discounts as engine
from app.engine import price_calculator as calc
from app.engine.products import matches, positive
from app.engine.tax import rub
from app.integrations.ozon.errors import (
    OzonAPIError,
    OzonAuthError,
    OzonNotConfiguredError,
    OzonTimeoutError,
)
from app.integrations.ozon.factory import get_ozon_client
from app.models import audit as audit_model
from app.models.discount import (
    APPLIED,
    APPROVE,
    BLOCKED,
    DECLINE,
    FAILED,
    FROM_SERVICE,
    PREVIEW,
    REJECTED,
    UNKNOWN,
    DiscountDecision,
    DiscountTask,
    DiscountTaskEvent,
)
from app.models.product import Product
from app.schemas.sync import utc
from app.services import audit, price_safety, pricing
from app.services.stocks import latest_stocks
from app.services.sync.parsing import to_id
from app.services.user_settings import discount_rule_margin, get_settings, low_margin

ENTITY = "discount_task"
APPROVE_ACTION, DECLINE_ACTION = "discount.approve", "discount.decline"
APPROVE_PATH = "/v1/actions/discounts-task/approve"
DECLINE_PATH = "/v1/actions/discounts-task/decline"
PREVIEW_TTL = timedelta(minutes=15)
MAX_TASKS = 500  # за одно решение
OZON_BATCH = 100  # заявок в одном запросе к Ozon
CONFIRM_ABOVE = price_safety.CONFIRM_ABOVE  # больше 10 — подтверждение (BUSINESS_LOGIC §8)
EXTREME_DISCOUNT_PCT = Decimal(70)
COMMENT_MAX = 500
CENT = Decimal("0.01")

NOT_FOUND = "Заявка не найдена"
GONE = "Предпросмотр устарел — рассчитайте заново."
USED = "Это решение уже отправлено в Ozon. Чтобы решить снова, сделайте новый предпросмотр."
NOT_PREVIEW = "Предпросмотр не найден — рассчитайте заново."


class DiscountError(Exception):
    """Решение нельзя выполнить. status_code — HTTP-статус ответа API, текст — для пользователя."""

    def __init__(self, status_code: int, message: str):
        self.status_code, self.message = status_code, message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _q(v: Decimal | None) -> Decimal | None:
    return None if v is None else Decimal(v).quantize(CENT, ROUND_HALF_UP)


# ── расчёт строк ──
@dataclass
class Context:
    now: datetime
    pricing: pricing.PricingContext
    products: dict[uuid.UUID, Product]
    on_hand: dict[uuid.UUID, int]
    low_margin: Decimal
    rule_margin: Decimal | None

    def params(self, product: Product | None) -> calc.PriceParams | None:
        return self.pricing.product_params(product) if product is not None else None


async def load_context(db: AsyncSession, user_id: uuid.UUID, product_ids: set[uuid.UUID] | None = None) -> Context:
    q = select(Product).where(Product.user_id == user_id)
    if product_ids is not None:
        q = q.where(Product.id.in_(product_ids or [uuid.uuid4()]))
    products = {p.id: p for p in (await db.execute(q)).scalars()}
    ctx = await pricing.load(db, user_id, list(products.values()))
    on_hand: dict[uuid.UUID, int] = {}
    for s, p, _ in await latest_stocks(db, user_id):
        if p.id in products:
            on_hand[p.id] = on_hand.get(p.id, 0) + max(s.present or 0, 0)
    settings = await get_settings(db, user_id)
    return Context(_now(), ctx, products, on_hand, low_margin(settings), discount_rule_margin(settings))


@dataclass
class Calc:
    """Расчёт при цене `price` (запрошенной покупателем или своей)."""

    price: Decimal | None  # ваша цена в Ozon сейчас
    breakeven: Decimal | None  # минимальная цена для участия (прибыль 0)
    target: Decimal | None  # при целевой марже
    margin: Decimal | None
    unit_profit: Decimal | None
    missing: list[str]
    cost_floor: Decimal | None  # себестоимость с НДС × 1,05
    commission_pct: Decimal | None


def calculate(ctx: Context, product: Product | None, price: Decimal | None) -> Calc:
    if product is None:
        return Calc(None, None, None, None, None, ["Товара нет в каталоге магазина — обновите товары."], None, None)
    inputs = ctx.pricing.inputs(product)
    params = ctx.pricing.params(inputs)
    snap = ctx.pricing.prices.get(product.id)
    current = positive(snap.price) if snap else None
    floor = (None if inputs.cost is None
             else (inputs.cost * inputs.supplier_vat * price_safety.COST_FLOOR).quantize(CENT, ROUND_HALF_UP))
    out = Calc(current, None, None, None, None, list(inputs.missing or []), floor,
               snap.commission_pct if snap else None)
    if params is None:
        return out
    for attr, margin in (("breakeven", Decimal(0)), ("target", params.target_margin_pct)):
        try:
            setattr(out, attr, calc.price_for_margin(params, margin))
        except calc.PriceUnreachable:
            pass
    if price is not None and price > 0:
        b = calc.breakdown(params, price)
        out.margin, out.unit_profit = b.margin, _q(b.profit)
    return out


def row(t: DiscountTask, ctx: Context, last_decision: DiscountDecision | None = None) -> dict:
    """Строка списка: заявка, ваша цена, расчёт при запрошенной цене, пометки, срок."""
    product = ctx.products.get(t.product_id) if t.product_id else None
    c = calculate(ctx, product, t.requested_price)
    state = engine.state(t.status, utc(t.end_at), ctx.now)
    qty = t.requested_quantity_max
    approved = calculate(ctx, product, t.approved_price) if t.approved_price else None
    marks = engine.marks(t.requested_price, c.breakeven, t.min_auto_price, c.margin, ctx.low_margin)
    return {
        "id": t.id, "ozon_task_id": t.ozon_task_id, "sku": t.sku, "name": t.name,
        "product": ({"id": product.id, "offer_id": product.offer_id, "name": product.name, "sku": product.ozon_sku,
                     "barcode": product.barcode} if product else None),
        "status": t.status, "state": state, "state_title": engine.STATE_TITLES.get(state, state),
        "can_decide": state == engine.NEW,
        "created_at": utc(t.created_at_ozon), "end_at": utc(t.end_at), "edited_till": utc(t.edited_till),
        "hours_left": engine.hours_left(utc(t.end_at), ctx.now) if state == engine.NEW else None,
        "requested_price": t.requested_price, "requested_discount_pct": t.requested_discount_pct,
        "discount_pct": engine.discount_pct(c.price, t.requested_price),
        "requested_quantity": qty, "price": c.price, "original_price": t.original_price,
        "min_auto_price": t.min_auto_price, "breakeven_price": c.breakeven, "target_price": c.target,
        "margin": c.margin, "unit_profit": c.unit_profit,
        "total_profit": None if c.unit_profit is None or not qty else _q(c.unit_profit * qty),
        "stock": ctx.on_hand.get(product.id) if product else None,
        "marks": marks, "mark_titles": [engine.MARK_TITLES[m] for m in marks],
        "rule_ok": engine.rule_ok(c.margin, ctx.rule_margin), "missing": c.missing,
        "approved_price": t.approved_price, "approved_quantity": t.approved_quantity_max,
        "approved_margin": approved.margin if approved else None,
        "moderated_at": utc(t.moderated_at), "is_auto_moderated": t.is_auto_moderated,
        "first_seen_at": utc(t.first_seen_at),
        "decision": None if last_decision is None else {
            "action": last_decision.action, "status": last_decision.status, "at": utc(last_decision.created_at),
            "price": last_decision.approved_price, "quantity": last_decision.approved_quantity_max,
            "error": last_decision.error,
        },
    }


async def _last_decisions(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, DiscountDecision]:
    rows = (await db.execute(
        select(DiscountDecision).where(DiscountDecision.user_id == user_id, DiscountDecision.status != PREVIEW)
        .order_by(DiscountDecision.created_at)
    )).scalars()
    return {d.task_id: d for d in rows}


async def all_rows(db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID | None = None) -> list[dict]:
    q = select(DiscountTask).where(DiscountTask.user_id == user_id)
    if product_id is not None:
        q = q.where(DiscountTask.product_id == product_id)
    tasks = list((await db.execute(q)).scalars())
    if not tasks:
        return []
    ctx = await load_context(db, user_id, {t.product_id for t in tasks if t.product_id})
    decisions = await _last_decisions(db, user_id)
    return [row(t, ctx, decisions.get(t.id)) for t in tasks]


# ── список ──
TABS = ("new", "approved", "declined", "expired", "all")
TAB_STATE = {"new": engine.NEW, "approved": engine.APPROVED, "declined": engine.DECLINED, "expired": engine.EXPIRED}
MARK_FILTERS = (engine.LOSS, engine.BELOW_OZON_MIN, engine.LOW_MARGIN, "rule_ok", "no_calc")


def _mark_ok(r: dict, mark: str) -> bool:
    if mark == "rule_ok":
        return r["rule_ok"] is True
    if mark == "no_calc":
        return r["margin"] is None
    return mark in r["marks"]


def _in_period(r: dict, date_from, date_to) -> bool:
    created = r["created_at"] or r["first_seen_at"]
    if created is None:
        return True
    from app.services.sync.finance import MSK

    day = created.astimezone(MSK).date()
    return (date_from is None or day >= date_from) and (date_to is None or day <= date_to)


SORTS = {
    "created_at": lambda r: r["created_at"] or r["first_seen_at"], "end_at": lambda r: r["end_at"],
    "requested_price": lambda r: r["requested_price"], "price": lambda r: r["price"],
    "discount_pct": lambda r: r["discount_pct"], "breakeven_price": lambda r: r["breakeven_price"],
    "margin": lambda r: r["margin"], "unit_profit": lambda r: r["unit_profit"],
    "total_profit": lambda r: r["total_profit"], "stock": lambda r: r["stock"],
    "requested_quantity": lambda r: r["requested_quantity"],
    "offer_id": lambda r: (r["product"] or {}).get("offer_id") or r["name"],
}


def sort_rows(rows: list[dict], sort: str | None, order: str) -> list[dict]:
    """Без сортировки — сначала те, что скорее истекут, потом новые сверху; пустые значения — в конце."""
    if not sort or sort not in SORTS:
        far = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(rows, key=lambda r: (not r["can_decide"], r["end_at"] or far if r["can_decide"] else far,
                                           -(r["created_at"] or r["first_seen_at"]).timestamp()))
    key = SORTS[sort]
    have = [r for r in rows if key(r) is not None]
    none = [r for r in rows if key(r) is None]
    return sorted(have, key=key, reverse=order == "desc") + none


def list_page(rows: list[dict], *, tab: str = "new", search: str | None = None, date_from=None, date_to=None,
              marks: list[str] | None = None, sort: str | None = None, order: str = "desc") -> dict:
    """Строки вкладки и счётчики вкладок (по поиску, периоду и пометкам — без вкладки)."""
    base = [r for r in rows if _in_period(r, date_from, date_to)
            and (not search or matches(search, *(r["product"] or {}).values(), r["name"], r["sku"]))
            and all(_mark_ok(r, m) for m in marks or [])]
    counts = {tab_: sum(1 for r in base if r["state"] == st) for tab_, st in TAB_STATE.items()}
    counts["all"] = len(base)
    counts["loss"] = sum(1 for r in base if r["state"] == engine.NEW and engine.LOSS in r["marks"])
    picked = base if tab == "all" else [r for r in base if r["state"] == TAB_STATE[tab]]
    return {"items": sort_rows(picked, sort, order), "counts": counts}


# ── предпросмотр решения ──
@dataclass
class Item:
    task_id: uuid.UUID
    action: str  # approve | decline
    price: Decimal | None = None  # своя цена (по умолчанию — запрошенная)
    quantity: int | None = None  # сколько штук (по умолчанию — сколько просят)


def check(t: DiscountTask, it: Item, ctx: Context) -> dict:
    """Что уйдёт в Ozon и можно ли: errors блокируют, needs_loss_ack — нужно «понимаю»."""
    errors: list[str] = []
    warnings: list[str] = []
    state = engine.state(t.status, utc(t.end_at), ctx.now)
    if state == engine.EXPIRED:
        errors.append("Срок заявки прошёл — решать её уже поздно.")
    elif state != engine.NEW:
        errors.append(f"Заявка уже {engine.STATE_TITLES.get(state, state).lower()} — решить её снова нельзя.")
    product = ctx.products.get(t.product_id) if t.product_id else None
    price = qty = None
    c = calculate(ctx, product, None)
    needs_loss_ack = False
    if it.action == APPROVE:
        price = it.price if it.price is not None else t.requested_price
        qty = it.quantity if it.quantity is not None else t.requested_quantity_max
        c = calculate(ctx, product, price)
        if product is None:
            errors.append("Товара заявки нет в каталоге магазина — обновите товары, тогда можно будет проверить цену.")
        if price is None or price <= 0:
            errors.append("Укажите цену — число больше 0.")
        else:
            if t.requested_price is not None and price < t.requested_price:
                errors.append(f"Цена ниже той, что просит покупатель ({rub(t.requested_price)}), — так не нужно.")
            if c.price is None:
                errors.append("Нет текущей цены товара из Ozon — обновите товары, тогда можно будет проверить скидку.")
            elif price >= c.price:
                errors.append(f"Цена не ниже вашей текущей ({rub(c.price)}) — это не скидка.")
            if c.commission_pct is not None and c.commission_pct >= price_safety.ANOMALY_COMMISSION_PCT:
                errors.append(f"Комиссия Ozon по товару {c.commission_pct} % выглядит ошибкой данных — ничего не меняем.")
            if product is not None and c.cost_floor is None:
                errors.append("Нет себестоимости товара — без неё не проверить, что цена не ниже себестоимости. "
                              "Укажите её в карточке товара.")
            elif c.cost_floor is not None and price < c.cost_floor:
                errors.append(f"Цена ниже себестоимости с НДС + 5 % ({rub(c.cost_floor)}) — так нельзя, это защита от "
                              "ошибки.")
            if c.price:
                discount = (c.price - price) / c.price * 100
                if discount > EXTREME_DISCOUNT_PCT:
                    warnings.append(f"Скидка {discount.quantize(Decimal('0.1'))} % — больше 70 %: проверьте цену.")
            needs_loss_ack = (c.breakeven is not None and price < c.breakeven) or (c.unit_profit is not None
                                                                                  and c.unit_profit < 0)
            if needs_loss_ack:
                loss = f": {rub(-c.unit_profit)} с штуки" if c.unit_profit is not None and c.unit_profit < 0 else ""
                warnings.append(f"Цена ниже минимальной для участия ({rub(c.breakeven) if c.breakeven else '—'}) — "
                                f"каждая продажа в минус{loss}.")
            elif c.margin is not None and c.margin < ctx.low_margin:
                warnings.append(f"Маржа {c.margin} % — ниже вашего порога {ctx.low_margin} %.")
            if t.min_auto_price is not None and price < t.min_auto_price:
                warnings.append(f"Цена ниже минимальной цены в Ozon ({rub(t.min_auto_price)}).")
            if c.margin is None and product is not None:
                warnings.append("Маржу не посчитать: " + " ".join(c.missing or ["нет данных для расчёта."]))
        if qty is None or qty < 1:
            errors.append("Укажите количество — хотя бы 1 шт.")
        elif t.requested_quantity_max and qty > t.requested_quantity_max:
            errors.append(f"Покупатель просит не больше {t.requested_quantity_max} шт. — больше разрешить нельзя.")
        elif product is not None and qty > ctx.on_hand.get(product.id, 0):
            warnings.append(f"Разрешено {qty} шт., а на складах сейчас {ctx.on_hand.get(product.id, 0)}.")
    return {
        "task_id": t.id, "ozon_task_id": t.ozon_task_id, "action": it.action,
        "product": ({"id": product.id, "offer_id": product.offer_id, "name": product.name} if product else None),
        "name": t.name, "requested_price": t.requested_price, "requested_quantity": t.requested_quantity_max,
        "price": c.price, "approved_price": price, "approved_quantity": qty,
        "discount_pct": engine.discount_pct(c.price, price), "breakeven_price": c.breakeven,
        "margin": c.margin, "unit_profit": c.unit_profit,
        "total_profit": None if c.unit_profit is None or not qty else _q(c.unit_profit * qty),
        "errors": errors, "warnings": warnings, "needs_loss_ack": needs_loss_ack, "can_apply": not errors,
    }


def _summary(results: list[dict]) -> dict:
    ok = [r for r in results if r["can_apply"]]
    return {
        "total": len(results), "ok": len(ok), "blocked": len(results) - len(ok),
        "approve": sum(1 for r in ok if r["action"] == APPROVE), "decline": sum(1 for r in ok if r["action"] == DECLINE),
        "loss": sum(1 for r in ok if r["needs_loss_ack"]),
        "needs_confirm": len(ok) > CONFIRM_ABOVE,
    }


async def _own_tasks(db: AsyncSession, user_id: uuid.UUID, ids: list[uuid.UUID], lock: bool = False) -> dict[uuid.UUID, DiscountTask]:
    q = select(DiscountTask).where(DiscountTask.user_id == user_id, DiscountTask.id.in_(ids))
    if lock:
        q = q.with_for_update()
    return {t.id: t for t in (await db.execute(q)).scalars()}


async def preview(db: AsyncSession, user_id: uuid.UUID, items: list[Item], comment: str | None = None) -> dict:
    """Предпросмотр решения по заявкам (одной или списком). В Ozon ничего не уходит."""
    if not items:
        raise DiscountError(400, "Выберите хотя бы одну заявку.")
    if len(items) > MAX_TASKS:
        raise DiscountError(400, f"Не больше {MAX_TASKS} заявок за раз — разделите на несколько решений.")
    if len({i.task_id for i in items}) != len(items):
        raise DiscountError(400, "Одна заявка выбрана дважды — обновите страницу.")
    if any(i.action not in (APPROVE, DECLINE) for i in items):
        raise DiscountError(400, "Неизвестное действие — обновите страницу.")
    tasks = await _own_tasks(db, user_id, [i.task_id for i in items])
    if len(tasks) != len(items):
        raise DiscountError(404, NOT_FOUND)
    ctx = await load_context(db, user_id, {t.product_id for t in tasks.values() if t.product_id})
    comment = (comment or "").strip()[:COMMENT_MAX] or None
    batch_id, now = uuid.uuid4(), _now()
    results = []
    for it in items:
        res = check(tasks[it.task_id], it, ctx)
        rec = DiscountDecision(
            id=uuid.uuid4(), user_id=user_id, task_id=it.task_id, product_id=tasks[it.task_id].product_id,
            created_at=now, action=it.action, status=PREVIEW, batch_id=batch_id,
            approved_price=res["approved_price"], approved_quantity_max=res["approved_quantity"],
            seller_comment=comment, margin=res["margin"], unit_profit=res["unit_profit"],
            breakeven_price=res["breakeven_price"], errors=res["errors"] or None, warnings=res["warnings"] or None,
        )
        db.add(rec)
        results.append({**res, "preview_id": rec.id})
    await db.commit()
    return {"batch_id": batch_id, "expires_at": now + PREVIEW_TTL, "comment": comment, "items": results,
            "summary": _summary(results)}


# ── применение ──
def _ozon_failure(exc: OzonAPIError) -> tuple[str, str]:
    """(итог, текст) ошибки запроса к Ozon — для каждой заявки этого запроса."""
    if isinstance(exc, OzonNotConfiguredError):
        return FAILED, exc.message
    if isinstance(exc, OzonAuthError):
        return FAILED, f"{exc.message}. Проверьте, что у API-ключа есть права на акции."
    if isinstance(exc, OzonTimeoutError):
        return UNKNOWN, "Ozon не ответил вовремя — решение могло примениться. Обновите заявки через минуту и проверьте."
    detail = f": {exc.ozon_message}" if exc.ozon_message and exc.status_code and exc.status_code < 500 else ""
    return FAILED, f"{exc.message}{detail}. В Ozon ничего не изменилось."


def _fails(resp: dict) -> dict[int, str]:
    result = resp.get("result") if isinstance(resp.get("result"), dict) else resp
    out = {}
    for f in result.get("fail_details") or []:
        if isinstance(f, dict) and (tid := to_id(f.get("task_id"))):
            out[tid] = str(f.get("error_for_user") or "").strip() or "Ozon не принял решение, причину не сообщил"
    return out


def _body(action: str, d: DiscountDecision, t: DiscountTask) -> dict:
    item: dict = {"id": t.ozon_task_id}
    if action == APPROVE:
        item.update(approved_price=float(d.approved_price), approved_quantity_min=1,
                    approved_quantity_max=d.approved_quantity_max)
    item["seller_comment"] = d.seller_comment or ""
    return item


def _record(db: AsyncSession, user_id: uuid.UUID, t: DiscountTask, d: DiscountDecision, status: str,
            request: Request | None, **kw) -> None:
    audit.record(
        db, user_id, action=APPROVE_ACTION if d.action == APPROVE else DECLINE_ACTION, entity_type=ENTITY,
        entity_id=str(t.ozon_task_id), product_id=t.product_id, status=status,
        before={"status": t.status, "requested_price": str(t.requested_price) if t.requested_price else None,
                "requested_quantity": t.requested_quantity_max},
        after={"status": "APPROVED" if d.action == APPROVE else "DECLINED",
               "approved_price": str(d.approved_price) if d.approved_price is not None else None,
               "approved_quantity": d.approved_quantity_max, "comment": d.seller_comment},
        request=request, **kw,
    )


async def apply(db: AsyncSession, user_id: uuid.UUID, batch_id: uuid.UUID, *, confirmed: bool = False,
                accept_loss: bool = False, request: Request | None = None) -> dict:
    """Отправить решения предпросмотра в Ozon. Итог — по каждой заявке."""
    previews = list((await db.execute(select(DiscountDecision).where(
        DiscountDecision.user_id == user_id, DiscountDecision.batch_id == batch_id, DiscountDecision.status == PREVIEW,
    ))).scalars())
    if not previews:
        raise DiscountError(404, NOT_PREVIEW)
    if utc(previews[0].created_at) + PREVIEW_TTL < _now():
        raise DiscountError(410, GONE)
    used = await db.scalar(select(DiscountDecision.id).where(
        DiscountDecision.preview_id.in_([p.id for p in previews])).limit(1))
    if used is not None:
        raise DiscountError(409, USED)
    usable = [p for p in previews if not p.errors]
    if not usable:
        raise DiscountError(400, "Нечего отправлять: все заявки заблокированы проверками.")
    if len(usable) > CONFIRM_ABOVE and not confirmed:
        raise DiscountError(400, f"Решение по {len(usable)} заявкам — подтвердите, что проверили список.")

    # проверки заново: заявку могли решить в кабинете Ozon, цена и себестоимость могли измениться
    tasks = await _own_tasks(db, user_id, [p.task_id for p in usable], lock=True)
    ctx = await load_context(db, user_id, {t.product_id for t in tasks.values() if t.product_id})
    now = _now()
    attempts: list[tuple[DiscountDecision, DiscountTask]] = []
    results: dict[uuid.UUID, dict] = {}
    for p in usable:
        t = tasks[p.task_id]
        res = check(t, Item(t.id, p.action, p.approved_price, p.approved_quantity_max), ctx)
        errors = list(res["errors"])
        if res["needs_loss_ack"] and not accept_loss:
            errors.append("Цена в минус — подтвердите, что понимаете: каждая продажа будет убыточной.")
        attempt = DiscountDecision(
            id=uuid.uuid4(), user_id=user_id, task_id=t.id, product_id=t.product_id, created_at=now, action=p.action,
            status=BLOCKED if errors else UNKNOWN, batch_id=batch_id, preview_id=p.id,
            approved_price=p.approved_price, approved_quantity_max=p.approved_quantity_max,
            seller_comment=p.seller_comment, margin=res["margin"], unit_profit=res["unit_profit"],
            breakeven_price=res["breakeven_price"], accept_loss=accept_loss and res["needs_loss_ack"],
            errors=errors or None, warnings=res["warnings"] or None,
        )
        db.add(attempt)
        if errors:
            _record(db, user_id, t, attempt, audit_model.REJECTED, request, error=" ".join(errors))
            results[t.id] = _result(attempt, t, " ".join(errors))
        else:
            attempts.append((attempt, t))
    try:
        await db.commit()  # попытки — до запроса в Ozon: упадём посреди запроса — история скажет «могло примениться»
    except IntegrityError:
        await db.rollback()
        raise DiscountError(409, USED) from None

    if attempts:
        try:
            client = await get_ozon_client(user_id, db, retry=False)
        except OzonAPIError as exc:
            status, text = _ozon_failure(exc)
            for attempt, t in attempts:
                attempt.status, attempt.error = status, text
                _record(db, user_id, t, attempt, status, request, error=text)
                results[t.id] = _result(attempt, t, text)
            attempts = []
        else:
            async with client:
                for action, path in ((APPROVE, APPROVE_PATH), (DECLINE, DECLINE_PATH)):
                    group = [(a, t) for a, t in attempts if a.action == action]
                    for start in range(0, len(group), OZON_BATCH):
                        await _send(db, user_id, client, path, group[start:start + OZON_BATCH], results, request)
    await db.commit()
    ordered = [results[p.task_id] for p in usable if p.task_id in results]
    applied = sum(1 for r in ordered if r["status"] == APPLIED)
    return {"batch_id": batch_id, "items": ordered, "applied": applied, "failed": len(ordered) - applied,
            "message": _message(ordered)}


async def _send(db: AsyncSession, user_id: uuid.UUID, client, path: str,
                group: list[tuple[DiscountDecision, DiscountTask]], results: dict, request: Request | None) -> None:
    body = {"tasks": [_body(a.action, a, t) for a, t in group]}
    req = {"method": "POST", "path": path, "body": body}
    try:
        resp = await client.request("POST", path, body)
        resp = resp if isinstance(resp, dict) else {}
    except OzonAPIError as exc:
        status, text = _ozon_failure(exc)
        for a, t in group:
            a.status, a.error = status, text
            _record(db, user_id, t, a, status, request, ozon_request=req, error=text,
                    ozon_response={"status_code": exc.status_code, "message": exc.ozon_message})
            results[t.id] = _result(a, t, text)
        return
    fails = _fails(resp)
    now = _now()
    for a, t in group:
        if t.ozon_task_id in fails:
            a.status, a.error = REJECTED, f"Ozon не принял решение: {fails[t.ozon_task_id]}"
            _record(db, user_id, t, a, audit_model.REJECTED, request, ozon_request=req, ozon_response=resp,
                    error=a.error)
            results[t.id] = _result(a, t, a.error)
            continue
        a.status = APPLIED
        _record(db, user_id, t, a, audit_model.APPLIED, request, ozon_request=req, ozon_response=resp)
        # последнее состояние заявки — как после решения; Ozon подтвердит при следующей загрузке (событие ozon)
        t.status = "APPROVED" if a.action == APPROVE else "DECLINED"
        if a.action == APPROVE:
            t.approved_price, t.approved_quantity_max = a.approved_price, a.approved_quantity_max
        t.moderated_at, t.is_auto_moderated = now, False
        db.add(DiscountTaskEvent(task_id=t.id, status=t.status, approved_price=t.approved_price if a.action == APPROVE
                                 else None, approved_quantity_max=a.approved_quantity_max if a.action == APPROVE
                                 else None, moderated_at=now, is_auto_moderated=False, source=FROM_SERVICE,
                                 decision_id=a.id, seen_at=now))
        results[t.id] = _result(a, t, None)


STATUS_TITLES = {
    APPLIED: "Принято Ozon", BLOCKED: "Не отправлено: не прошло проверки", REJECTED: "Ozon отказал",
    FAILED: "Ошибка запроса — ничего не изменилось", UNKNOWN: "Ozon не ответил — могло примениться",
}


def _result(d: DiscountDecision, t: DiscountTask, error: str | None) -> dict:
    return {"task_id": t.id, "ozon_task_id": t.ozon_task_id, "action": d.action, "status": d.status,
            "status_title": STATUS_TITLES.get(d.status, d.status), "approved_price": d.approved_price,
            "approved_quantity": d.approved_quantity_max, "error": error}


def _message(items: list[dict]) -> str:
    ok = [i for i in items if i["status"] == APPLIED]
    approved = sum(1 for i in ok if i["action"] == APPROVE)
    declined = len(ok) - approved
    parts = []
    if approved:
        parts.append(f"одобрено {approved}")
    if declined:
        parts.append(f"отклонено {declined}")
    head = ("В Ozon: " + ", ".join(parts) + ".") if parts else "Ozon ничего не принял."
    bad = len(items) - len(ok)
    return head + (f" Не прошло: {bad} — причины у каждой заявки." if bad else "")


# ── заявка подробно ──
async def detail(db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> dict:
    t = await db.get(DiscountTask, task_id)
    if t is None or t.user_id != user_id:
        raise DiscountError(404, NOT_FOUND)
    ctx = await load_context(db, user_id, {t.product_id} if t.product_id else set())
    events = list((await db.execute(select(DiscountTaskEvent).where(DiscountTaskEvent.task_id == t.id)
                                    .order_by(DiscountTaskEvent.seen_at))).scalars())
    decisions = list((await db.execute(select(DiscountDecision).where(
        DiscountDecision.task_id == t.id, DiscountDecision.status != PREVIEW).order_by(DiscountDecision.created_at))).scalars())
    return {
        **row(t, ctx, decisions[-1] if decisions else None),
        "events": [{"at": utc(e.seen_at), "status": e.status, "title": engine.STATE_TITLES.get(e.status, e.status),
                    "approved_price": e.approved_price, "approved_quantity": e.approved_quantity_max,
                    "source": e.source, "is_auto_moderated": e.is_auto_moderated} for e in events],
        "decisions": [{"at": utc(d.created_at), "action": d.action, "status": d.status,
                       "status_title": STATUS_TITLES.get(d.status, d.status), "approved_price": d.approved_price,
                       "approved_quantity": d.approved_quantity_max, "comment": d.seller_comment, "error": d.error,
                       "errors": d.errors or [], "margin": d.margin} for d in decisions],
    }


async def new_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Сколько заявок ждут решения (новые и не истёкшие) — для меню и главной."""
    now = _now()
    rows = (await db.execute(select(DiscountTask.status, DiscountTask.end_at).where(
        DiscountTask.user_id == user_id, DiscountTask.status == engine.NEW))).all()
    return sum(1 for s, e in rows if engine.state(s, utc(e), now) == engine.NEW)
