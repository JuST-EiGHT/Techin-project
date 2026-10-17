"""Изменение цены одного товара в Ozon (шаг 6.2) — первое изменение цен.

Порядок (правило 6, BUSINESS_LOGIC §8): предпросмотр → применение только по preview_id → журнал.

- Предпросмотр (`preview`): текущая цена — свежая из Ozon (/v5/product/info/prices; изменилась — новый снимок, как
  при синхронизации; Ozon сейчас недоступен — по последнему снимку, с предупреждением), проверки `price_safety`,
  было → станет, маржа и «с 1 шт.» калькулятором 4.2. Запись `price_changes` со status preview — её id и есть
  preview_id, живёт 15 минут. В Ozon ничего не меняется.
- Применение (`apply`): предпросмотр свой, не устарел, ещё не применялся и без ошибок. Цена — снова свежая из Ozon
  (не получили — не применяем) и не изменилась после предпросмотра; проверки — заново. Попытка — новая запись
  `price_changes` с preview_id (уникально: один предпросмотр — одна попытка), сначала со status unknown: если
  процесс упадёт во время запроса, запись честно говорит «могло примениться». Затем /v1/product/import/prices и
  итог: applied — новый снимок цены, rejected — Ozon отказал по товару, failed — ошибка запроса (ничего не
  изменилось), unknown — Ozon не ответил. Каждая попытка, и отказ проверок тоже, — в audit_log: до и после, запрос и
  ответ Ozon, IP, User-Agent.
- Меняем только цену. Минимальную и зачёркнутую — нет: зачёркнутую — в кабинете Ozon (решение владельца 17.10.2026),
  цена не ниже неё блокируется проверкой.

Установка цены идемпотентна (повтор ставит ту же цену), поэтому обычные повторы клиента Ozon при сбое сети не вредят.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import price_calculator as calc
from app.engine.products import positive
from app.engine.tax import rub
from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import (
    OzonAPIError,
    OzonAuthError,
    OzonNotConfiguredError,
    OzonTimeoutError,
)
from app.integrations.ozon.factory import get_ozon_client
from app.models import audit as audit_model
from app.models.price_change import APPLIED, BLOCKED, FAILED, MANUAL_SINGLE, PREVIEW, REJECTED, UNKNOWN, PriceChange
from app.models.product import Product, ProductPrice
from app.schemas.sync import utc
from app.services import audit, price_safety, pricing
from app.services.sync.finance import MSK
from app.services.sync.parsing import to_id
from app.services.sync.products import PRICE_FIELDS, changed_snapshot, fetch_prices, latest_prices, price_attrs

PREVIEW_TTL = timedelta(minutes=15)
IMPORT_PATH = "/v1/product/import/prices"
ACTION = "price.update"
CENT = Decimal("0.01")

ARCHIVED = "Товар в архиве Ozon — сначала верните его из архива в кабинете Ozon."
NOT_IN_OZON = "Ozon не вернул цену товара — проверьте, что товар есть в кабинете Ozon, и обновите товары."
PROCESSING = "Ozon обработает за 1–3 минуты — потом новая цена появится на сайте и в сервисе."

# Ошибки Ozon по товару (docs/ozon, «Частые ошибки» /v1/product/import/prices) — понятным текстом
OZON_ERRORS = {
    "old_price_less_than_price": "цена до скидки (зачёркнутая) должна быть выше цены — измените её в кабинете Ozon",
    "price_less_than_min_auto_price": "цена должна быть выше минимальной цены товара",
    "min_auto_price_too_big": "минимальная цена должна быть ниже цены",
    "min_auto_price_too_small": "минимальная цена должна быть не меньше 50 % цены",
    "price_out_of_range": (
        "цена выходит за порог категории товара — Ozon советует среднерыночную цену; другую можно поставить через "
        "поддержку Ozon"
    ),
    "action_price_enabled_min_price_missing": (
        "у товара включено автоприменение акций с минимальной ценой — отключите его в кабинете Ozon"
    ),
    "price_negative": "цена не может быть отрицательной",
    "NOT_FOUND_ERROR": "товара с таким идентификатором нет в кабинете Ozon",
}
RATE_LIMIT_HINT = "limit exceeded"  # «error limiting: acquire limit per item: items limit: limit exceeded»


class PriceUpdateError(Exception):
    """Изменение нельзя выполнить или Ozon его не выполнил. status_code — HTTP-статус ответа API."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(Decimal(v).quantize(CENT))


def _when(dt: datetime) -> str:
    return f"{utc(dt).astimezone(MSK):%d.%m.%Y %H:%M}"


async def fresh_prices(
    db: AsyncSession, client: OzonClient, products: list[Product]
) -> dict[uuid.UUID, ProductPrice | None]:
    """Цены товаров прямо сейчас из Ozon (один запрос на 1000 товаров); изменилась — новый снимок (правило 12).
    Товар, которого Ozon не вернул, — None."""
    items = await fetch_prices(client, [p.ozon_product_id for p in products])
    last = await latest_prices(db, [p.id for p in products])
    now = _now()
    out: dict[uuid.UUID, ProductPrice | None] = {}
    for p in products:
        item = items.get(p.ozon_product_id)
        if item is None:
            out[p.id] = None
            continue
        snap = changed_snapshot(p.id, last.get(p.id), price_attrs(item), now)
        if snap is not None:
            db.add(snap)
        out[p.id] = snap or last.get(p.id)
    await db.flush()
    return out


async def _fresh_price(db: AsyncSession, client: OzonClient, product: Product) -> ProductPrice | None:
    return (await fresh_prices(db, client, [product]))[product.id]


def auth_text(exc: OzonAuthError) -> str:
    return f"{exc.message}. Проверьте, что у API-ключа есть права на цены товаров."


def margins(ctx: pricing.PricingContext, product: Product, before: Decimal | None, after: Decimal) -> dict:
    """Маржа и «с 1 шт.» при прежней и новой цене, минимальная и целевая — калькулятором 4.2."""
    inputs = ctx.inputs(product)
    params = ctx.params(inputs)
    out = {"profit_before": None, "margin_before": None, "profit_after": None, "margin_after": None,
           "min_price": None, "target_price": None, "margin_note": None}
    if params is None:
        out["margin_note"] = "Маржу не посчитать: " + " ".join(inputs.missing)
        return out
    result = calc.calculate(params)
    out["min_price"], out["target_price"] = result.min_price, result.target_price
    if before is not None:
        b = calc.breakdown(params, before)
        out["profit_before"], out["margin_before"] = b.profit, b.margin
    a = calc.breakdown(params, after)
    out["profit_after"], out["margin_after"] = a.profit, a.margin
    return out


async def preview(
    db: AsyncSession, user_id: uuid.UUID, product: Product, new_price: Decimal, reason: str | None = None
) -> dict:
    """Что будет, если поставить цену, — и можно ли. Сохраняется как предпросмотр (preview_id), в Ozon не меняем."""
    warnings: list[str] = []
    fresh = True
    try:
        async with await get_ozon_client(user_id, db) as client:
            snap = await _fresh_price(db, client, product)
    except OzonNotConfiguredError as exc:
        raise PriceUpdateError(400, exc.message) from exc
    except OzonAuthError as exc:
        raise PriceUpdateError(403, auth_text(exc)) from exc
    except OzonAPIError as exc:
        fresh = False
        snap = (await latest_prices(db, [product.id])).get(product.id)
        warnings.append(
            f"{exc.message}: цена — по данным "
            + (f"на {_when(snap.snapshot_at)}" if snap is not None else "последней синхронизации")
            + ". Перед применением сервис проверит её в Ozon ещё раз."
        )
    [(_, safety)] = await price_safety.check(db, user_id, [price_safety.Proposal(product, new_price)])
    errors = list(safety.errors)
    if product.is_archived:
        errors.insert(0, ARCHIVED)
    if fresh and snap is None:
        errors.insert(0, NOT_IN_OZON)
    warnings = safety.warnings + warnings
    cur = positive(snap.price) if snap is not None else None

    now = _now()
    row = PriceChange(
        id=uuid.uuid4(), user_id=user_id, product_id=product.id, offer_id=product.offer_id, created_at=now,
        change_type=MANUAL_SINGLE, reason=(reason or "").strip() or None, price_before=cur, price_after=new_price,
        min_price_before=positive(snap.min_price) if snap is not None else None, is_dry_run=True, is_applied=False,
        status=PREVIEW, errors=errors or None, warnings=warnings or None,
    )
    db.add(row)
    ctx = await pricing.load(db, user_id, [product])
    await db.commit()
    return {
        "preview_id": row.id, "created_at": now, "expires_at": now + PREVIEW_TTL, "can_apply": not errors,
        "errors": errors, "warnings": warnings,
        "product": {"id": product.id, "offer_id": product.offer_id, "name": product.name},
        "price_before": cur, "price_after": new_price,
        "old_price": positive(snap.old_price) if snap is not None else None,
        "min_price_ozon": row.min_price_before,
        "change_pct": safety.change_pct,
        "change_rub": (new_price - cur).quantize(CENT) if cur is not None else None,
        "floor": safety.floor, "allowed_from": safety.allowed_from, "allowed_to": safety.allowed_to,
        "next_change_at": safety.next_change_at,
        "price_checked_at": now if fresh else (utc(snap.snapshot_at) if snap is not None else None),
        "price_fresh": fresh, "reason": row.reason, **margins(ctx, product, cur, new_price),
    }


def state(price: Decimal | None, min_price: Decimal | None = None) -> dict:
    return {"price": _s(price), "min_price": _s(min_price)}


def ozon_failure(exc: OzonAPIError) -> tuple[str, str, int, str]:
    """(итог в price_changes, итог в журнале, HTTP-статус, текст) для ошибки запроса к Ozon."""
    if isinstance(exc, OzonAuthError):
        return FAILED, audit_model.FAILED, 403, auth_text(exc) + " Цена не изменилась."
    if isinstance(exc, OzonTimeoutError):
        return UNKNOWN, audit_model.UNKNOWN, 504, (
            "Ozon не ответил вовремя — цена могла измениться. Через 1–3 минуты обновите товары и проверьте цену."
        )
    detail = f": {exc.ozon_message}" if exc.ozon_message and exc.status_code and exc.status_code < 500 else ""
    return FAILED, audit_model.FAILED, 502, f"{exc.message}{detail}. В Ozon ничего не изменилось."


def result_for(resp: dict, product: Product) -> tuple[bool, list[dict]]:
    """Итог по товару из ответа Ozon: обновлено ли и ошибки [{code, message}]."""
    for item in resp.get("result") or []:
        if not isinstance(item, dict):
            continue
        if to_id(item.get("product_id")) == product.ozon_product_id or (
            not item.get("product_id") and item.get("offer_id") == product.offer_id
        ):
            return bool(item.get("updated")), [e for e in item.get("errors") or [] if isinstance(e, dict)]
    return False, []


def ozon_reasons(errors: list[dict]) -> str:
    texts = []
    for e in errors:
        code = str(e.get("code") or "").strip()
        message = str(e.get("message") or "").strip()
        if code in OZON_ERRORS:
            texts.append(OZON_ERRORS[code])
        elif RATE_LIMIT_HINT in f"{code} {message}":
            texts.append("цену этого товара меняли слишком часто — Ozon разрешает не больше 10 раз в час")
        else:
            texts.append(": ".join(t for t in (code, message) if t) or "без пояснения")
    return "; ".join(texts)


async def load_preview(db: AsyncSession, user_id: uuid.UUID, preview_id: uuid.UUID) -> PriceChange:
    pv = await db.get(PriceChange, preview_id)
    if pv is None or pv.user_id != user_id or pv.status != PREVIEW:
        raise PriceUpdateError(404, "Предпросмотр не найден — рассчитайте последствия заново.")
    if pv.batch_id is not None:  # часть массового изменения — применяется только целиком (подтверждение, очередь)
        raise PriceUpdateError(400, "Это часть массового изменения цен — примените его на странице «Цены списком».")
    return pv


async def apply(db: AsyncSession, user_id: uuid.UUID, preview_id: uuid.UUID, request: Request | None = None) -> dict:
    """Применить предпросмотр: проверки заново → запрос в Ozon → price_changes, audit_log, снимок новой цены."""
    pv = await load_preview(db, user_id, preview_id)
    if await db.scalar(select(PriceChange.id).where(PriceChange.preview_id == pv.id)) is not None:
        raise PriceUpdateError(
            409, "Этот предпросмотр уже применяли. Чтобы изменить цену ещё раз, рассчитайте последствия заново."
        )
    if _now() - utc(pv.created_at) > PREVIEW_TTL:
        raise PriceUpdateError(
            410, "Предпросмотр устарел (действует 15 минут) — рассчитайте последствия заново: цена в Ozon могла "
            "измениться."
        )
    if pv.errors:
        raise PriceUpdateError(
            400, "Предпросмотр показал ошибки — такое изменение применить нельзя. " + " ".join(pv.errors)
        )
    product = await db.get(Product, pv.product_id)
    new_price: Decimal = pv.price_after
    attempt = PriceChange(
        id=uuid.uuid4(), user_id=user_id, product_id=product.id, offer_id=product.offer_id, created_at=_now(),
        change_type=pv.change_type, reason=pv.reason, price_before=pv.price_before, price_after=new_price,
        min_price_before=pv.min_price_before, is_dry_run=False, is_applied=False, status=UNKNOWN, preview_id=pv.id,
    )

    def log(status: str, **kw) -> None:
        audit.record(
            db, user_id, action=ACTION, entity_type=audit_model.PRICE_ENTITY, entity_id=str(attempt.id),
            product_id=product.id, status=status, before=state(attempt.price_before, attempt.min_price_before),
            after=state(new_price, attempt.min_price_before), request=request, **kw,
        )

    async def stop(status: str, journal: str, code: int, text: str, **kw) -> None:
        attempt.status, attempt.error = status, text
        db.add(attempt)
        log(journal, error=text, **kw)
        await db.commit()
        raise PriceUpdateError(code, text)

    try:
        async with await get_ozon_client(user_id, db) as client:
            try:
                snap = await _fresh_price(db, client, product)
            except OzonAPIError as exc:
                code, text = (403, auth_text(exc)) if isinstance(exc, OzonAuthError) else (502, exc.message)
                await stop(FAILED, audit_model.FAILED, code, f"Не удалось проверить текущую цену в Ozon: {text}. "
                           "Ничего не меняли — попробуйте через минуту.")
            # одно изменение товара за раз: вторая попытка ждёт и видит первую (пауза час)
            await db.execute(select(Product.id).where(Product.id == product.id).with_for_update())
            errors: list[str] = []
            if product.is_archived:
                errors.append(ARCHIVED)
            cur = positive(snap.price) if snap is not None else None
            if snap is None:
                errors.append(NOT_IN_OZON)
            elif cur != pv.price_before:
                was = rub(pv.price_before) if pv.price_before else "—"
                errors.append(
                    f"Цена в Ozon изменилась после предпросмотра: была {was}, сейчас {rub(cur) if cur else '—'} — "
                    "рассчитайте последствия заново."
                )
            elif positive(snap.min_price) != pv.min_price_before:
                errors.append(
                    "Минимальная цена в Ozon изменилась после предпросмотра — рассчитайте последствия заново."
                )
            [(_, safety)] = await price_safety.check(db, user_id, [price_safety.Proposal(product, new_price)])
            errors += safety.errors
            attempt.warnings = safety.warnings or None
            if errors:
                attempt.errors = errors
                await stop(BLOCKED, audit_model.REJECTED, 400, " ".join(errors))

            db.add(attempt)  # «могло примениться», пока Ozon не ответил; уникальный preview_id — одна попытка
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise PriceUpdateError(409, "Этот предпросмотр уже применяют — обновите страницу.") from exc

            body = {"prices": [{"product_id": product.ozon_product_id, "price": f"{new_price:.2f}",
                                "currency_code": "RUB"}]}
            req = {"method": "POST", "path": IMPORT_PATH, "body": body}
            try:
                data = await client.request("POST", IMPORT_PATH, body)
            except OzonAPIError as exc:
                status, journal, code, text = ozon_failure(exc)
                await stop(status, journal, code, text, ozon_request=req,
                           ozon_response={"status_code": exc.status_code, "message": exc.ozon_message})
    except OzonNotConfiguredError as exc:
        raise PriceUpdateError(400, exc.message) from exc

    resp = data if isinstance(data, dict) else {}
    updated, ozon_errors = result_for(resp, product)
    if not updated:
        reasons = ozon_reasons(ozon_errors)
        await stop(REJECTED, audit_model.REJECTED, 409,
                   "Ozon не изменил цену" + (f": {reasons}." if reasons else ", причину не сообщил."),
                   ozon_request=req, ozon_response=resp)

    now = _now()
    last = (await latest_prices(db, [product.id])).get(product.id)
    attrs = {f: getattr(last, f) if last is not None else None for f in PRICE_FIELDS}
    if (snap_new := changed_snapshot(product.id, last, {**attrs, "price": new_price}, now)) is not None:
        db.add(snap_new)  # то, что поставили; синхронизация добавит снимок, если Ozon в итоге покажет другое
    attempt.status, attempt.is_applied = APPLIED, True
    log(audit_model.APPLIED, ozon_request=req, ozon_response=resp)
    await db.commit()
    return {
        "change_id": attempt.id, "status": APPLIED, "applied": True,
        "product": {"id": product.id, "offer_id": product.offer_id, "name": product.name},
        "price_before": attempt.price_before, "price_after": new_price, "warnings": safety.warnings,
        "message": f"Цена {rub(new_price)} отправлена в Ozon. {PROCESSING}",
    }
