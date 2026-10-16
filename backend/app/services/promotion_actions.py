"""Вход и выход из акции Ozon (шаг 4.3) — первое изменение данных в Ozon.

Правило 6 и BUSINESS_LOGIC §8: сначала предпросмотр — ничего не меняется, в Ozon не ходим; применение — только с
confirm=true и после той же проверки заново; каждая попытка применения (и отказ наших проверок) — в audit_log с
данными до и после, запросом и ответом Ozon, IP и User-Agent.

Методы — по документации Ozon (docs/ozon/seller_api_2026-10.txt): вход и смена цены или количества —
POST /v1/actions/products/update, выход — POST /v2/actions/products/deactivate (v1 activate/deactivate отключены
13.10.2026). Из «Эластичного» и «Максимального бустинга» Ozon выводит через цену в акции, а не выходом — их выход
здесь не делаем, только подсказываем.

Жёсткие проверки (блокируют, пользователь не отключает):
- акция не закончилась и не заморожена (после freeze_date Ozon не даёт менять список товаров и повышать цены);
- товар — кандидат или участник этой акции по последней синхронизации;
- цена в акции > 0, ниже обычной и не выше максимальной цены Ozon для участия;
- цена в акции не ниже себестоимости с НДС × 1,05; без себестоимости — не проверить, значит нельзя (правило 5);
- данные Ozon не выглядят аномально: есть обычная цена, комиссия < 90 %;
- одну пару товар × акция не меняем чаще раза в час (выход не ограничен — он убирает скидку);
- маржа в минус — только с явным «понимаю» (accept_loss).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import price_calculator as calc
from app.engine.products import positive
from app.engine.promotions import is_boosting
from app.engine.tax import rub
from app.integrations.ozon.errors import (
    OzonAPIError,
    OzonAuthError,
    OzonNotConfiguredError,
    OzonTimeoutError,
)
from app.integrations.ozon.factory import get_ozon_client
from app.models.audit import APPLIED, FAILED, REJECTED, UNKNOWN
from app.models.product import Product
from app.models.promotion import Promotion, PromotionProduct
from app.schemas.promotion import WHOLE_STOCK_WARNING
from app.schemas.sync import utc
from app.services import audit, pricing
from app.services.stocks import latest_stocks
from app.services.sync.finance import MSK
from app.services.sync.parsing import to_id
from app.services.sync.promotions import snapshot

JOIN, UPDATE, LEAVE = "promotion.join", "promotion.update", "promotion.leave"
ENTITY = "promotion_product"
UPDATE_PATH = "/v1/actions/products/update"
DEACTIVATE_PATH = "/v2/actions/products/deactivate"
COST_FLOOR = Decimal("1.05")  # цена не ниже себестоимости × 1,05 (BUSINESS_LOGIC §8)
PAUSE = timedelta(hours=1)  # пауза между изменениями одной пары товар × акция
EXTREME_DISCOUNT_PCT = Decimal(70)  # Ozon помечает скидку больше 70 % как «слишком низкую цену»
SUSPICIOUS_COMMISSION_PCT = Decimal(90)
CENT = Decimal("0.01")


class ActionError(Exception):
    """Действие нельзя выполнить или Ozon его не выполнил. status_code — HTTP-статус ответа API."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


@dataclass
class Target:
    promo: Promotion
    product: Product
    current: PromotionProduct | None  # участвует сейчас
    candidate: PromotionProduct | None  # может участвовать
    on_hand: int  # доступно на всех складах

    @property
    def entity_id(self) -> str:
        return f"{self.promo.ozon_action_id}:{self.product.ozon_product_id}"


async def load_target(db: AsyncSession, user_id: uuid.UUID, promotion_id: uuid.UUID, product_id: uuid.UUID) -> Target:
    promo = await db.get(Promotion, promotion_id)
    if promo is None or promo.user_id != user_id:
        raise ActionError(404, "Акция не найдена")
    product = await db.get(Product, product_id)
    if product is None or product.user_id != user_id:
        raise ActionError(404, "Товар не найден")
    rows = (await db.execute(
        select(PromotionProduct).where(
            PromotionProduct.promotion_id == promo.id, PromotionProduct.product_id == product.id,
            PromotionProduct.left_at.is_(None),
        )
    )).scalars().all()
    current = next((r for r in rows if r.is_active), None)
    candidate = next((r for r in rows if not r.is_active), None)
    on_hand = sum(max(s.present or 0, 0) for s, _, _ in await latest_stocks(db, user_id, product_id=product.id))
    return Target(promo, product, current, candidate, on_hand)


def _state(row: PromotionProduct | None) -> dict:
    if row is None:
        return {"participating": False, "action_price": None, "stock": None}
    return {"participating": True, "action_price": _s(row.action_price), "stock": row.stock}


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(Decimal(v).quantize(CENT))


def _common_errors(t: Target) -> list[str]:
    now = datetime.now(timezone.utc)
    errors = []
    date_to = utc(t.promo.date_to)
    if date_to is not None and date_to < now:
        errors.append("Акция уже закончилась.")
    freeze = utc(t.promo.freeze_date)
    if freeze is not None and freeze <= now:
        errors.append(
            f"С {freeze.astimezone(MSK):%d.%m.%Y} акция заморожена: Ozon не даёт менять в ней "
            "список товаров и повышать цены."
        )
    return errors


async def join_preview(
    db: AsyncSession, user_id: uuid.UUID, t: Target, action_price: Decimal | None, stock: int | None
) -> dict:
    """Что будет, если войти в акцию (или изменить цену и количество участника), — и можно ли."""
    errors = _common_errors(t)
    warnings: list[str] = []
    row = t.current or t.candidate
    mode = UPDATE if t.current is not None else JOIN
    whole = t.promo.whole_stock
    ctx = await pricing.load(db, user_id, [t.product])
    inputs = ctx.inputs(t.product)
    params = ctx.params(inputs)
    snap = ctx.prices.get(t.product.id)
    regular = positive(snap.price) if snap else None

    if row is None:
        errors.append(
            "Ozon не предлагает этот товар в этой акции — его нет среди кандидатов. Если это не так, обновите акции."
        )
    max_price = positive(row.max_action_price) if row else None
    if action_price is None and row is not None:
        action_price = (
            positive(t.current.action_price) if t.current is not None
            else max_price or positive(row.action_price)
        )
    if not whole:
        if stock is None and row is not None:
            stock = t.current.stock if t.current is not None and t.current.stock else (
                row.recommended_stock or row.min_stock or max(t.on_hand, 1)
            )
    else:
        stock = None  # «весь остаток»: количество ограничить нельзя (BUSINESS_LOGIC §9)

    if regular is None:
        errors.append("Нет текущей цены товара из Ozon — обновите товары, тогда можно будет проверить цену в акции.")
    if snap is not None and snap.commission_pct is not None and snap.commission_pct >= SUSPICIOUS_COMMISSION_PCT:
        errors.append(f"Комиссия Ozon по товару {snap.commission_pct} % выглядит ошибкой данных — ничего не меняем.")
    if action_price is None or action_price <= 0:
        errors.append("Укажите цену в акции — число больше 0.")
    else:
        if max_price is not None and action_price > max_price:
            errors.append(f"Цена в акции — не выше {rub(max_price)}: выше Ozon в эту акцию не принимает.")
        if regular is not None and action_price >= regular:
            errors.append(f"Цена в акции должна быть ниже обычной цены {rub(regular)}.")
        if inputs.cost is None:
            errors.append(
                "Нет себестоимости товара — без неё не проверить, что цена в акции не ниже себестоимости. "
                "Укажите её в карточке товара."
            )
        else:
            floor = (inputs.cost * inputs.supplier_vat * COST_FLOOR).quantize(CENT, ROUND_HALF_UP)
            if action_price < floor:
                errors.append(
                    f"Цена в акции ниже себестоимости с НДС + 5 % ({rub(floor)}) — так нельзя, это защита от ошибки."
                )
        if regular is not None and regular > 0:
            discount = (regular - action_price) / regular * 100
            if discount > EXTREME_DISCOUNT_PCT:
                warnings.append(f"Скидка {discount.quantize(Decimal('0.1'))} % — больше 70 %: проверьте цену.")
    if not whole and row is not None:
        if stock is None or stock < 1:
            errors.append("Укажите количество — сколько штук продать по акционной цене.")
        elif row.min_stock and stock < row.min_stock:
            errors.append(f"Ozon требует заявить в акцию не меньше {row.min_stock} шт.")
        elif stock > t.on_hand:
            warnings.append(f"Заявлено {stock} шт., а на складах сейчас {t.on_hand} — продастся не больше, чем есть.")
    if t.current is not None and action_price is not None and t.current.action_price == action_price and (
        whole or t.current.stock == stock
    ):
        errors.append("Товар уже участвует в акции с такой ценой и количеством — менять нечего.")
    last = await audit.last_applied(db, user_id, ENTITY, t.entity_id, (JOIN, UPDATE), PAUSE)
    if last is not None:
        minutes = max(int((datetime.now(timezone.utc) - last).total_seconds() // 60), 0)
        errors.append(
            f"Этот товар в этой акции меняли {minutes} мин. назад — следующее изменение не раньше чем через час "
            "после прошлого (защита от ошибок)."
        )

    b = calc.breakdown(params, action_price) if params is not None and action_price and action_price > 0 else None
    if b is None and action_price:
        warnings.append("Маржу не посчитать: " + " ".join(inputs.missing or ["нет данных для расчёта."]))
    needs_loss_ack = b is not None and b.profit < 0
    if needs_loss_ack:
        warnings.append(f"Каждая продажа по этой цене — в минус: {rub(-b.profit)} с штуки.")
    whole_total = None
    if whole:
        warnings.append(WHOLE_STOCK_WARNING)
        if b is not None:
            whole_total = {"qty": t.on_hand, "profit": _s(b.profit * t.on_hand)}
    after = {"participating": True, "action_price": _s(action_price), "stock": stock}
    return {
        "action": "update" if mode == UPDATE else "join", "applied": False, "can_apply": not errors,
        "errors": errors, "warnings": warnings, "needs_loss_ack": needs_loss_ack,
        "before": _state(t.current), "after": after,
        "action_price": action_price, "stock": stock, "stock_required": not whole,
        "min_stock": row.min_stock if row else None, "recommended_stock": row.recommended_stock if row else None,
        "max_action_price": max_price, "price": regular,
        "discount_pct": None if not regular or not action_price else ((regular - action_price) / regular * 100).quantize(Decimal("0.1")),
        "margin": b.margin if b else None, "unit_profit": b.profit if b else None,
        "min_price": calc.calculate(params).min_price if params is not None else None,
        "on_hand": t.on_hand, "whole_stock": whole, "whole_stock_total": whole_total,
        "boosting": is_boosting(t.promo.title, t.promo.action_type), "message": None,
    }


async def leave_preview(db: AsyncSession, user_id: uuid.UUID, t: Target) -> dict:
    errors = _common_errors(t)
    warnings: list[str] = []
    if t.current is None:
        errors.append("Товар сейчас не участвует в этой акции.")
    boosting = is_boosting(t.promo.title, t.promo.action_type)
    if boosting:
        errors.append(
            f"Из «{t.promo.title}» Ozon убирает товар не выходом, а через цену в акции — сделайте это в личном "
            "кабинете Ozon: «Акции» → эта акция."
        )
    if t.current is not None and t.current.added_automatically:
        warnings.append("Товар добавил Ozon автоматически — при следующем автодобавлении он может добавить его снова.")
    return {
        "action": "leave", "applied": False, "can_apply": not errors, "errors": errors, "warnings": warnings,
        "needs_loss_ack": False, "before": _state(t.current), "after": _state(None),
        "action_price": t.current.action_price if t.current else None, "stock": t.current.stock if t.current else None,
        "stock_required": False, "min_stock": None, "recommended_stock": None,
        "max_action_price": None, "price": None, "discount_pct": None, "margin": None, "unit_profit": None,
        "min_price": None, "on_hand": t.on_hand, "whole_stock": t.promo.whole_stock, "whole_stock_total": None,
        "boosting": boosting, "message": None,
    }


def _ids(values) -> set[int]:
    return {i for v in values or [] if (i := to_id(v))}


def _reason(items, product_id: int) -> str | None:
    for it in items or []:
        if isinstance(it, dict) and to_id(it.get("product_id")) == product_id:
            return str(it.get("reason") or "").strip() or None
    return None


async def _call_ozon(db: AsyncSession, user_id: uuid.UUID, path: str, body: dict) -> dict:
    async with await get_ozon_client(user_id, db) as client:
        data = await client.request("POST", path, body)
    return data if isinstance(data, dict) else {}


def _ozon_failure(exc: OzonAPIError) -> tuple[str, int, str]:
    """(статус журнала, HTTP-статус, текст) для ошибки запроса к Ozon."""
    if isinstance(exc, OzonNotConfiguredError):
        return FAILED, 400, exc.message
    if isinstance(exc, OzonAuthError):
        return FAILED, 403, f"{exc.message}. Проверьте, что у API-ключа есть права на изменение акций."
    if isinstance(exc, OzonTimeoutError):
        return UNKNOWN, 504, (
            "Ozon не ответил вовремя — изменение могло примениться. Обновите акции через минуту и проверьте."
        )
    detail = f": {exc.ozon_message}" if exc.ozon_message and exc.status_code and exc.status_code < 500 else ""
    return FAILED, 502, f"{exc.message}{detail}. В Ozon ничего не изменилось."


async def apply_join(
    db: AsyncSession, user_id: uuid.UUID, t: Target, action_price: Decimal | None, stock: int | None,
    accept_loss: bool, request: Request | None,
) -> dict:
    preview = await join_preview(db, user_id, t, action_price, stock)
    action = UPDATE if preview["action"] == "update" else JOIN
    errors = list(preview["errors"])
    if preview["needs_loss_ack"] and not accept_loss:
        errors.append("Цена в минус — подтвердите, что понимаете: каждая продажа будет убыточной.")
    if errors:
        audit.record(db, user_id, action=action, entity_type=ENTITY, entity_id=t.entity_id, status=REJECTED,
                     before=preview["before"], after=preview["after"], error=" ".join(errors), request=request)
        await db.commit()
        raise ActionError(400, " ".join(errors))

    price: Decimal = preview["action_price"]
    item: dict = {"product_id": t.product.ozon_product_id, "action_price": {"amount": f"{price:.2f}", "currency": "RUB"}}
    if not t.promo.whole_stock:
        item["stock"] = str(preview["stock"])
    body = {"action_id": t.promo.ozon_action_id, "products": [item]}
    req = {"method": "POST", "path": UPDATE_PATH, "body": body}
    try:
        resp = await _call_ozon(db, user_id, UPDATE_PATH, body)
    except OzonAPIError as exc:
        status, code, text = _ozon_failure(exc)
        audit.record(db, user_id, action=action, entity_type=ENTITY, entity_id=t.entity_id, status=status,
                     before=preview["before"], after=preview["after"], ozon_request=req,
                     ozon_response={"status_code": exc.status_code, "message": exc.ozon_message}, error=text,
                     request=request)
        await db.commit()
        raise ActionError(code, text) from exc

    pid = t.product.ozon_product_id
    ok = pid in _ids(resp.get("active_product_ids")) and pid not in _ids(resp.get("deactivated_product_ids"))
    if not ok:
        reason = _reason(resp.get("rejected"), pid) or _reason(resp.get("warnings"), pid)
        text = f"Ozon не {'добавил товар в акцию' if action == JOIN else 'изменил товар в акции'}" + (
            f": {reason}" if reason else ", причину не сообщил") + "."
        audit.record(db, user_id, action=action, entity_type=ENTITY, entity_id=t.entity_id, status=REJECTED,
                     before=preview["before"], after=preview["after"], ozon_request=req, ozon_response=resp,
                     error=text, request=request)
        await db.commit()
        raise ActionError(409, text)

    now = datetime.now(timezone.utc)
    row = t.current
    if row is None:  # кандидат стал участником: строка кандидата заменяется строкой участия (с историей)
        cand = t.candidate
        values = {
            "price": cand.price if cand else None, "min_price": cand.min_price if cand else None,
            "max_action_price": cand.max_action_price if cand else None,
        }
        if cand is not None:
            await db.delete(cand)
            await db.flush()
        row = PromotionProduct(
            promotion_id=t.promo.id, product_id=t.product.id, action_price=price, stock=preview["stock"],
            is_active=True, added_automatically=False, margin_in_promo=preview["margin"], first_seen_at=now,
            synced_at=now, **values,
        )
        db.add(row)
        await db.flush()
    else:
        row.action_price, row.stock, row.margin_in_promo = price, preview["stock"], preview["margin"]
    snapshot(db, row, now)
    warn = [r for it in resp.get("warnings") or [] if isinstance(it, dict) and (r := str(it.get("reason") or "").strip())]
    audit.record(db, user_id, action=action, entity_type=ENTITY, entity_id=t.entity_id, status=APPLIED,
                 before=preview["before"], after=preview["after"], ozon_request=req, ozon_response=resp, request=request)
    await db.commit()
    message = (f"Товар добавлен в акцию по {rub(price)}" if action == JOIN else f"Цена в акции — {rub(price)}") + "."
    return {**preview, "applied": True, "warnings": preview["warnings"] + warn, "message": message}


async def apply_leave(db: AsyncSession, user_id: uuid.UUID, t: Target, request: Request | None) -> dict:
    preview = await leave_preview(db, user_id, t)
    if preview["errors"]:
        text = " ".join(preview["errors"])
        audit.record(db, user_id, action=LEAVE, entity_type=ENTITY, entity_id=t.entity_id, status=REJECTED,
                     before=preview["before"], after=preview["after"], error=text, request=request)
        await db.commit()
        raise ActionError(400, text)
    body = {"action_id": t.promo.ozon_action_id, "product_ids": [str(t.product.ozon_product_id)]}
    req = {"method": "POST", "path": DEACTIVATE_PATH, "body": body}
    try:
        resp = await _call_ozon(db, user_id, DEACTIVATE_PATH, body)
    except OzonAPIError as exc:
        status, code, text = _ozon_failure(exc)
        audit.record(db, user_id, action=LEAVE, entity_type=ENTITY, entity_id=t.entity_id, status=status,
                     before=preview["before"], after=preview["after"], ozon_request=req,
                     ozon_response={"status_code": exc.status_code, "message": exc.ozon_message}, error=text,
                     request=request)
        await db.commit()
        raise ActionError(code, text) from exc
    if t.product.ozon_product_id not in _ids(resp.get("product_ids")):
        reason = _reason(resp.get("rejected"), t.product.ozon_product_id)
        text = "Ozon не убрал товар из акции" + (f": {reason}" if reason else ", причину не сообщил") + "."
        audit.record(db, user_id, action=LEAVE, entity_type=ENTITY, entity_id=t.entity_id, status=REJECTED,
                     before=preview["before"], after=preview["after"], ozon_request=req, ozon_response=resp,
                     error=text, request=request)
        await db.commit()
        raise ActionError(409, text)
    t.current.left_at = datetime.now(timezone.utc)  # строка остаётся — это история участия
    audit.record(db, user_id, action=LEAVE, entity_type=ENTITY, entity_id=t.entity_id, status=APPLIED,
                 before=preview["before"], after=preview["after"], ozon_request=req, ozon_response=resp, request=request)
    await db.commit()
    return {**preview, "applied": True, "message": "Товар убран из акции."}
