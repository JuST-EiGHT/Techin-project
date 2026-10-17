"""Массовое изменение цен (шаг 6.3) — те же правила, что у одного товара (6.2, BUSINESS_LOGIC §8), пачкой.

- Откуда цены: стратегия по выбранным товарам — целевая маржа M % (цена калькулятором 4.2), +X % к текущей цене,
  минимальная цена + X %; или файл — шаблон с текущими ценами, колонка «Новая цена» (правило 14). Цена не меняется —
  товара нет в предпросмотре («без изменений»).
- «Не больше 20 % за раз» у стратегии (limit_step): цена дальше допустимого за один раз — ставим ближайшую допустимую,
  до цели — следующим шагом не раньше чем через час.
- Предпросмотр (`preview`): не больше 500 товаров; свежие цены из Ozon одним запросом (Ozon недоступен — по последним
  снимкам, с предупреждением); проверки §8 по каждому → «применится» / «с предупреждением» / «заблокировано»; записи
  price_changes (preview, manual_bulk) с общим batch_id — это id массового предпросмотра, живёт 15 минут.
- Применение (`apply`) — только по batch_id: больше 10 товаров — подтверждение, больше 50 — слово «ПОДТВЕРЖДАЮ»;
  товары с предупреждениями — только если их включили. Цены снова свежие из Ozon (не получили — ничего не меняем);
  изменилась после предпросмотра или проверки не прошли — blocked. Остальные встают в очередь (queued: часовая пауза
  уже их учитывает) и уходят в Ozon по 100 с паузой 2 с; ошибка одного запроса не останавливает следующие. Итог каждого
  товара — как у одного (applied / rejected / failed / unknown); каждый — в audit_log со своей частью запроса и ответа.
- Ход применения — `status(batch_id)`: страница опрашивает его, пока идёт запрос на применение.
"""
import asyncio
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import price_calculator as calc
from app.engine.products import positive
from app.engine.tax import num, rub
from app.integrations.ozon.errors import OzonAPIError, OzonAuthError, OzonNotConfiguredError
from app.integrations.ozon.factory import get_ozon_client
from app.integrations.xlsx.common import cell_text
from app.models import audit as audit_model
from app.models.price_change import (
    APPLIED,
    BLOCKED,
    MANUAL_BULK,
    PREVIEW,
    QUEUED,
    REJECTED,
    UNKNOWN,
    PriceChange,
)
from app.models.product import Product
from app.services import audit, price_safety, pricing
from app.services.price_updater import (
    ACTION,
    ARCHIVED,
    CENT,
    IMPORT_PATH,
    NOT_IN_OZON,
    PREVIEW_TTL,
    PROCESSING,
    PriceUpdateError,
    auth_text,
    fresh_prices,
    margins,
    ozon_failure,
    ozon_reasons,
    result_for,
    state,
)
from app.services.sync.products import PRICE_FIELDS, changed_snapshot, latest_prices
from app.services.templates import (
    ERROR_COLUMN,
    CellError,
    Column,
    TemplateError,
    TemplateSpec,
    build_file,
    echo,
    parse_key,
    parse_money,
    read_table,
)

TARGET_MARGIN, PERCENT, MIN_PLUS = "target_margin", "percent", "min_plus"
STRATEGIES = (TARGET_MARGIN, PERCENT, MIN_PLUS)
OK, WARN, BLOCK = "ok", "warn", "blocked"  # группы предпросмотра
BATCH_PAUSE_SEC: float = price_safety.OZON_BATCH_PAUSE_SEC  # между запросами в Ozon; в тестах — 0
MAX_FILE_ROWS = 20_000
MAX_PRICE = Decimal("100000000")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Strategy:
    kind: str  # target_margin | percent | min_plus
    value: Decimal  # маржа % или X %
    limit_step: bool = True  # дальше 20 % за раз — ближайшая допустимая цена

    def describe(self) -> str:
        v = num(self.value)
        if self.kind == TARGET_MARGIN:
            return f"Целевая маржа {v} %"
        if self.kind == PERCENT:
            return f"{'+' if self.value > 0 else ''}{v} % к текущей цене"
        return f"Минимальная цена + {v} %"


@dataclass
class Item:
    """Строка массового изменения: товар и цена, которую хотим поставить (None — не посчитать, причина в errors)."""

    product: Product
    price: Decimal | None
    errors: list[str] = field(default_factory=list)
    note: str | None = None  # пояснение к цене (стратегия «не больше 20 % за раз»)
    line: int | None = None  # строка файла


def _strategy_price(
    s: Strategy, ctx: pricing.PricingContext, product: Product, cur: Decimal | None
) -> tuple[Decimal | None, str | None]:
    if s.kind == PERCENT:
        if cur is None:
            return None, price_safety.NO_PRICE
        return (cur * (1 + s.value / 100)).quantize(CENT, ROUND_HALF_UP), None
    inputs = ctx.inputs(product)
    params = ctx.params(inputs)
    if params is None:
        return None, "Цену не посчитать: " + " ".join(inputs.missing)
    try:
        if s.kind == TARGET_MARGIN:
            return calc.price_for_margin(params, s.value), None
        base = calc.price_for_margin(params, Decimal(0))
    except calc.PriceUnreachable as exc:
        return None, str(exc)
    return (base * (1 + s.value / 100)).quantize(CENT, ROUND_HALF_UP), None


def strategy_items(
    s: Strategy, ctx: pricing.PricingContext, products: list[Product], facts: dict[uuid.UUID, price_safety.PriceCheck]
) -> list[Item]:
    """Цена по стратегии для каждого товара; «не больше 20 % за раз» — ближайшая допустимая."""
    out = []
    for p in products:
        c = facts[p.id]
        cur = positive(c.current_price)
        price, error = _strategy_price(s, ctx, p, cur)
        item = Item(p, price, [error] if error else [])
        if price is not None and s.limit_step and cur is not None:
            lo, hi = price_safety.allowed_range(c)
            if lo is not None and not lo <= price <= hi:
                step = hi if price > hi else lo
                item.note = (
                    f"По стратегии — {rub(price)}, за один раз можно до {rub(step)}: ставим её, остальное — следующим "
                    "шагом не раньше чем через час."
                )
                item.price = step
        out.append(item)
    return out


def _group(errors: list[str], warnings: list[str]) -> str:
    return BLOCK if errors else WARN if warnings else OK


def _product(p: Product) -> dict:
    return {"id": p.id, "offer_id": p.offer_id, "name": p.name}


async def _facts(
    db: AsyncSession, user_id: uuid.UUID, products: list[Product]
) -> dict[uuid.UUID, price_safety.PriceCheck]:
    checks = await price_safety.gather(db, user_id, [price_safety.Proposal(p) for p in products])
    return {c.product_id: c for c in checks}


async def _refresh(
    db: AsyncSession, user_id: uuid.UUID, products: list[Product], strict: bool
) -> tuple[dict, str | None]:
    """Свежие цены из Ozon. strict — без них нельзя (применение); иначе — по последним снимкам с предупреждением."""
    try:
        async with await get_ozon_client(user_id, db) as client:
            return await fresh_prices(db, client, products), None
    except OzonNotConfiguredError as exc:
        raise PriceUpdateError(400, exc.message) from exc
    except OzonAuthError as exc:
        raise PriceUpdateError(403, auth_text(exc)) from exc
    except OzonAPIError as exc:
        if strict:
            raise PriceUpdateError(
                502,
                f"Не удалось проверить текущие цены в Ozon: {exc.message}. Ничего не меняли — попробуйте через минуту.",
            ) from exc
        return {}, f"{exc.message}: цены — по последним данным. Перед применением сервис проверит их в Ozon ещё раз."


async def preview(
    db: AsyncSession, user_id: uuid.UUID, *, strategy: Strategy | None = None,
    product_ids: list[uuid.UUID] | None = None, prices: list[tuple[uuid.UUID, Decimal]] | None = None,
    reason: str | None = None, file_info: dict | None = None, file_items: list[Item] | None = None,
) -> dict:
    """Массовый предпросмотр: по стратегии (product_ids) или по списку цен (prices / file_items). В Ozon не меняем."""
    if file_items is not None:
        items_in = file_items
        products = [i.product for i in items_in]
    else:
        ids = list(dict.fromkeys(product_ids or [pid for pid, _ in prices or []]))
        rows = {p.id: p for p in (await db.execute(
            select(Product).where(Product.user_id == user_id, Product.id.in_(ids))
        )).scalars()} if ids else {}
        if missing := [i for i in ids if i not in rows]:
            raise PriceUpdateError(404, f"Товар не найден ({len(missing)} шт.) — обновите страницу.")
        products = [rows[i] for i in ids]
        items_in = None
    if not products:
        raise PriceUpdateError(400, "Не выбрано ни одного товара.")
    if len(products) > price_safety.MAX_PRODUCTS:
        raise PriceUpdateError(
            400, f"За один раз — не больше {price_safety.MAX_PRODUCTS} товаров, выбрано {len(products)}. "
            "Разделите на части."
        )

    snaps, stale = await _refresh(db, user_id, products, strict=False)
    facts = await _facts(db, user_id, products)
    ctx = await pricing.load(db, user_id, products)
    if strategy is not None:
        items = strategy_items(strategy, ctx, products, facts)
    elif items_in is not None:
        items = items_in
    else:
        wanted = {pid: price.quantize(CENT) for pid, price in prices or []}
        items = [Item(p, wanted[p.id]) for p in products]

    checker = price_safety.PriceSafetyChecker()
    now = _now()
    batch_id = uuid.uuid4()
    default = strategy.describe() if strategy else "Цены из файла" if file_items is not None else None
    description = (reason or "").strip() or default
    out: list[dict] = []
    unchanged = 0
    for item in items:
        c = facts[item.product.id]
        cur = positive(c.current_price)
        if item.price is not None and cur is not None and item.price == cur and not item.errors:
            unchanged += 1
            continue
        errors = list(item.errors)
        warnings: list[str] = []
        result = None
        if item.price is not None:
            result = checker.check(replace(c, new_price=item.price))
            errors += result.errors
            warnings += result.warnings
        if item.product.is_archived:
            errors.insert(0, ARCHIVED)
        if snaps and snaps.get(item.product.id) is None:
            errors.insert(0, NOT_IN_OZON)
        row = PriceChange(
            id=uuid.uuid4(), user_id=user_id, product_id=item.product.id, offer_id=item.product.offer_id,
            created_at=now, change_type=MANUAL_BULK, reason=description, price_before=cur, price_after=item.price,
            min_price_before=positive(c.current_min_price), is_dry_run=True, is_applied=False, status=PREVIEW,
            errors=errors or None, warnings=warnings or None, batch_id=batch_id,
        )
        db.add(row)
        figures = margins(ctx, item.product, cur, item.price) if item.price is not None else {}
        out.append({
            "preview_id": row.id, "product": _product(item.product), "group": _group(errors, warnings),
            "price_before": cur, "price_after": item.price, "change_pct": result.change_pct if result else None,
            "note": item.note, "line": item.line, "errors": errors, "warnings": warnings,
            "profit_before": figures.get("profit_before"), "profit_after": figures.get("profit_after"),
            "margin_before": figures.get("margin_before"), "margin_after": figures.get("margin_after"),
        })
    await db.commit()
    order = {OK: 0, WARN: 1, BLOCK: 2}
    out.sort(key=lambda i: (order[i["group"]], i["product"]["offer_id"]))
    groups = Counter(i["group"] for i in out)
    return {
        "batch_id": batch_id if out else None, "created_at": now, "expires_at": now + PREVIEW_TTL,
        "description": description, "strategy": strategy.kind if strategy else None, "price_fresh": stale is None,
        "warnings": [stale] if stale else [],
        "counts": {"ok": groups[OK], "warn": groups[WARN], "blocked": groups[BLOCK], "unchanged": unchanged,
                   "total": len(out)},
        "items": out, "file": file_info,
    }


# ── применение ──
async def _batch_rows(db: AsyncSession, user_id: uuid.UUID, batch_id: uuid.UUID) -> list[PriceChange]:
    rows = list((await db.execute(
        select(PriceChange).where(PriceChange.batch_id == batch_id, PriceChange.user_id == user_id,
                                  PriceChange.status == PREVIEW)
    )).scalars())
    if not rows:
        raise PriceUpdateError(404, "Массовый предпросмотр не найден — рассчитайте изменения заново.")
    return rows


async def _attempts(db: AsyncSession, previews: list[PriceChange]) -> list[PriceChange]:
    return list((await db.execute(
        select(PriceChange).where(PriceChange.preview_id.in_([p.id for p in previews]))
    )).scalars())


def _part(product: Product, price: Decimal) -> dict:
    return {"product_id": product.ozon_product_id, "price": f"{price:.2f}", "currency_code": "RUB"}


async def apply(
    db: AsyncSession, user_id: uuid.UUID, batch_id: uuid.UUID, *, include_warnings: bool = False,
    confirmed: bool = False, confirm_word: str | None = None, request: Request | None = None,
) -> dict:
    """Применить массовый предпросмотр: проверки заново → очередь → Ozon по 100 с паузой → итог по каждому товару."""
    previews = await _batch_rows(db, user_id, batch_id)
    if await _attempts(db, previews):
        raise PriceUpdateError(
            409, "Это массовое изменение уже применяли. Чтобы изменить цены ещё раз, рассчитайте заново."
        )
    if _now() - min(_utc(p.created_at) for p in previews) > PREVIEW_TTL:
        raise PriceUpdateError(410, "Предпросмотр устарел (действует 15 минут) — рассчитайте изменения заново: цены в "
                                    "Ozon могли измениться.")
    chosen = [p for p in previews if not p.errors and (include_warnings or not p.warnings)]
    if not chosen:
        raise PriceUpdateError(400, "Применять нечего: у всех товаров ошибки"
                                    + ("" if include_warnings else " или предупреждения (их можно включить)") + ".")
    checker = price_safety.PriceSafetyChecker()
    gate = checker.check_request([p.offer_id for p in chosen], confirmed, confirm_word)
    if gate.errors:
        raise PriceUpdateError(400, " ".join(gate.errors))
    products = {p.id: p for p in (await db.execute(
        select(Product).where(Product.id.in_([c.product_id for c in chosen]))
    )).scalars()}

    try:
        async with await get_ozon_client(user_id, db) as client:
            try:
                snaps = await fresh_prices(db, client, list(products.values()))
            except OzonAPIError as exc:
                text = auth_text(exc) if isinstance(exc, OzonAuthError) else exc.message
                raise PriceUpdateError(403 if isinstance(exc, OzonAuthError) else 502,
                                       f"Не удалось проверить текущие цены в Ozon: {text}. Ничего не меняли — "
                                       "попробуйте через минуту.") from exc
            # одно изменение товара за раз: параллельные изменения ждут и упираются в часовую паузу
            await db.execute(
                select(Product.id).where(Product.id.in_(list(products))).order_by(Product.id).with_for_update()
            )
            checks = {c.product_id: c for c in await price_safety.gather(
                db, user_id, [price_safety.Proposal(products[p.product_id], p.price_after) for p in chosen]
            )}
            now = _now()
            attempts: list[tuple[PriceChange, Product]] = []
            for pv in chosen:
                product = products[pv.product_id]
                snap = snaps.get(product.id)
                cur = positive(snap.price) if snap is not None else None
                errors: list[str] = []
                if product.is_archived:
                    errors.append(ARCHIVED)
                if snap is None:
                    errors.append(NOT_IN_OZON)
                elif cur != pv.price_before:
                    was = rub(pv.price_before) if pv.price_before else "—"
                    errors.append(
                        f"Цена в Ozon изменилась после предпросмотра: была {was}, сейчас {rub(cur) if cur else '—'} — "
                        "рассчитайте заново."
                    )
                result = checker.check(checks[product.id])
                errors += result.errors
                attempt = PriceChange(
                    id=uuid.uuid4(), user_id=user_id, product_id=product.id, offer_id=product.offer_id, created_at=now,
                    change_type=MANUAL_BULK, reason=pv.reason, price_before=pv.price_before, price_after=pv.price_after,
                    min_price_before=pv.min_price_before, is_dry_run=False, is_applied=False,
                    status=BLOCKED if errors else QUEUED, errors=errors or None, warnings=result.warnings or None,
                    preview_id=pv.id, batch_id=batch_id,
                )
                db.add(attempt)
                if errors:
                    _log(db, user_id, attempt, product, audit_model.REJECTED, request, error=" ".join(errors))
                else:
                    attempts.append((attempt, product))
            attempts.sort(key=lambda ap: ap[1].offer_id)
            size = price_safety.OZON_BATCH
            for i, (attempt, _) in enumerate(attempts):
                attempt.batch_no = i // size + 1
            try:
                await db.commit()  # очередь сохранена: часовая пауза её учитывает; уникальный preview_id — одна попытка
            except IntegrityError as exc:
                await db.rollback()
                raise PriceUpdateError(409, "Это массовое изменение уже применяют — обновите страницу.") from exc

            chunks = [attempts[i:i + size] for i in range(0, len(attempts), size)]
            for n, chunk in enumerate(chunks, start=1):
                if n > 1:
                    await asyncio.sleep(BATCH_PAUSE_SEC)
                await _send(db, client, user_id, n, chunk, request)
    except OzonNotConfiguredError as exc:
        raise PriceUpdateError(400, exc.message) from exc
    return await status(db, user_id, batch_id)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _log(db: AsyncSession, user_id: uuid.UUID, attempt: PriceChange, product: Product, journal: str,
         request: Request | None, **kw) -> None:
    audit.record(
        db, user_id, action=ACTION, entity_type=audit_model.PRICE_ENTITY, entity_id=str(attempt.id),
        product_id=product.id, status=journal, before=state(attempt.price_before, attempt.min_price_before),
        after=state(attempt.price_after, attempt.min_price_before), request=request, **kw,
    )


async def _send(db: AsyncSession, client, user_id: uuid.UUID, n: int, chunk: list[tuple[PriceChange, Product]],
                request: Request | None) -> None:
    """Один запрос в Ozon (до 100 товаров): «отправляется» → итог каждого товара, журнал, снимки применённых цен."""
    for attempt, _ in chunk:
        attempt.status = UNKNOWN  # отправляется: ответа ещё нет
    await db.commit()
    body = {"prices": [_part(p, a.price_after) for a, p in chunk]}
    try:
        data = await client.request("POST", IMPORT_PATH, body)
    except OzonAPIError as exc:
        status, journal, _, text = ozon_failure(exc)
        for attempt, product in chunk:
            attempt.status, attempt.error = status, text
            _log(db, user_id, attempt, product, journal, request, error=text,
                 ozon_request={"method": "POST", "path": IMPORT_PATH, "batch_no": n,
                               "body": {"prices": [_part(product, attempt.price_after)]}},
                 ozon_response={"status_code": exc.status_code, "message": exc.ozon_message})
        await db.commit()
        return
    resp = data if isinstance(data, dict) else {}
    last = await latest_prices(db, [p.id for _, p in chunk])
    now = _now()
    for attempt, product in chunk:
        updated, errors = result_for(resp, product)
        req = {"method": "POST", "path": IMPORT_PATH, "batch_no": n,
               "body": {"prices": [_part(product, attempt.price_after)]}}
        mine = [r for r in resp.get("result") or [] if isinstance(r, dict) and r.get("product_id") is not None
                and str(r.get("product_id")) == str(product.ozon_product_id)]
        part = {"result": mine}
        if updated:
            attempt.status, attempt.is_applied = APPLIED, True
            prev = last.get(product.id)
            attrs = {f: getattr(prev, f) if prev is not None else None for f in PRICE_FIELDS}
            if (snap := changed_snapshot(product.id, prev, {**attrs, "price": attempt.price_after}, now)) is not None:
                db.add(snap)
            _log(db, user_id, attempt, product, audit_model.APPLIED, request, ozon_request=req, ozon_response=part)
        else:
            reasons = ozon_reasons(errors)
            attempt.status = REJECTED
            attempt.error = "Ozon не изменил цену" + (f": {reasons}." if reasons else ", причину не сообщил.")
            _log(db, user_id, attempt, product, audit_model.REJECTED, request, error=attempt.error, ozon_request=req,
                 ozon_response=part)
    await db.commit()


# ── ход применения ──
async def status(db: AsyncSession, user_id: uuid.UUID, batch_id: uuid.UUID) -> dict:
    """Что с массовым изменением: по каждому товару и по каждому запросу в Ozon."""
    previews = await _batch_rows(db, user_id, batch_id)
    attempts = {a.preview_id: a for a in await _attempts(db, previews)}
    products = {p.id: p for p in (await db.execute(
        select(Product).where(Product.id.in_([p.product_id for p in previews]))
    )).scalars()}
    items, batches = [], {}
    for pv in sorted(previews, key=lambda p: p.offer_id):
        a = attempts.get(pv.id)
        st = a.status if a else "not_applied"
        if a is not None and a.status == UNKNOWN and a.error is None:
            st = "sending"
        items.append({
            "preview_id": pv.id, "product": _product(products[pv.product_id]), "status": st,
            "price_before": pv.price_before, "price_after": pv.price_after, "batch_no": a.batch_no if a else None,
            "error": (a.error or " ".join(a.errors or [])) if a else None,
        })
        if a is not None and a.batch_no is not None:
            b = batches.setdefault(a.batch_no, Counter())
            b[st] += 1
    batch_list = []
    for no in sorted(batches):
        c = batches[no]
        state_ = "waiting" if c[QUEUED] == sum(c.values()) else "sending" if c["sending"] or c[QUEUED] else "done"
        batch_list.append({"no": no, "size": sum(c.values()), "state": state_, "applied": c[APPLIED],
                           "rejected": c[REJECTED], "failed": c["failed"], "unknown": c[UNKNOWN]})
    counts = Counter(i["status"] for i in items)
    started = bool(attempts)
    finished = started and not counts[QUEUED] and not counts["sending"]
    return {
        "batch_id": batch_id, "started": started, "finished": finished, "total": len(items),
        "counts": {k: counts[k] for k in ("applied", "rejected", "failed", "unknown", "blocked", "queued", "sending",
                                          "not_applied")},
        "batches": batch_list, "items": items,
        "message": f"Цены отправлены в Ozon: {counts[APPLIED]}. {PROCESSING}" if finished and counts[APPLIED] else None,
    }


# ── Excel: шаблон с текущими ценами → «Новая цена» → загрузить (правило 14) ──
NEW_PRICE = "Новая цена, руб."
PRICE_SPEC = TemplateSpec(
    sheet="Цены",
    columns=(
        Column(
            "offer_id", "Артикул", width=28, aliases=("offer_id", "артикул продавца", "артикул", "article"),
            exclude=("справочно",), hint="Артикул продавца — как в личном кабинете Ozon. Не меняйте.",
        ),
        Column("name", "Название (справочно)", width=44, reference=True,
               hint="Название товара — для удобства, при загрузке не читается."),
        Column("price", "Цена сейчас, руб. (справочно)", kind="money", width=16, reference=True,
               hint="Цена в Ozon на момент выгрузки — для сравнения, при загрузке не читается. Перед изменением сервис "
                    "ещё раз проверит цену в Ozon."),
        Column("min_price", "Минимальная по расчёту (справочно)", kind="money", width=16, reference=True,
               hint="Цена без убытка по расчёту цены (комиссия, логистика, налоги, себестоимость) — для ориентира."),
        Column("target_price", "Целевая по расчёту (справочно)", kind="money", width=16, reference=True,
               hint="Цена с вашей целевой маржой по расчёту цены — для ориентира."),
        Column(
            "new_price", NEW_PRICE, kind="money", width=16, aliases=("new_price", "новая цена", "новая"),
            exclude=("справочно",),
            hint="Цену, которую поставить в Ozon, — число больше 0, например 1250,50. Не меняете — оставьте как есть "
                 "или сотрите: такие строки пропускаются.",
        ),
    ),
    required=(("offer_id",), ("new_price",)),
    missing_columns=(
        "Не найдены колонки «Артикул» и «Новая цена». Скачайте шаблон на странице «Цены списком» — в нём уже есть ваши "
        "товары и текущие цены — или назовите колонки так же."
    ),
    instructions=(
        f"1. В колонке «{NEW_PRICE}» исправьте цены, которые хотите поменять. Остальные строки не трогайте.",
        "2. Сохраните файл и загрузите его на странице «Цены списком». Сначала покажем, что изменится, и проверим "
        "каждую цену — в Ozon уйдёт только после подтверждения.",
        "Правила те же, что у одного товара: не больше 20 % за раз, не ниже себестоимости с НДС + 5 %, ниже "
        "зачёркнутой цены, не чаще раза в час; за раз — не больше 500 изменённых цен.",
    ),
    notes=(
        "Частые ошибки",
        "• Не меняйте заголовки и артикулы — по ним мы находим колонки и товары.",
        "• Цена с копейками — через запятую: 1250,50.",
        "• Формулы можно, но сохраните файл в Excel или Google Таблицах, чтобы в нём были посчитанные значения.",
    ),
)
TEMPLATE_FORMATS = ("xlsx", "csv")


async def template(db: AsyncSession, user_id: uuid.UUID, fmt: str, filters: set[str]) -> bytes:
    """Шаблон с товарами в продаже и текущими ценами; «Новая цена» = текущая — файл без правок ничего не меняет."""
    rows, _ = await pricing.table(db, user_id)
    rows = sorted((r for r in rows if pricing.passes(r, filters)), key=lambda r: r["offer_id"])
    return build_file(fmt, PRICE_SPEC, [
        {"offer_id": r["offer_id"], "name": r["name"], "price": r["price"], "min_price": r["min_price"],
         "target_price": r["target_price"], "new_price": r["price"]}
        for r in rows
    ])


@dataclass
class FileRow:
    line: int
    offer_id: str
    raw: dict[str, Any]
    price: Decimal | None = None
    product: Product | None = None
    error: str | None = None


async def parse_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> tuple[list[FileRow], str]:
    """Строки файла с ошибками разбора. Пустая или такая же цена — не ошибка: строка просто не меняется."""
    try:
        table = read_table(PRICE_SPEC, content, filename, MAX_FILE_ROWS)
    except TemplateError as exc:
        raise PriceUpdateError(400, str(exc)) from exc
    products = {p.offer_id: p for p in (await db.execute(select(Product).where(Product.user_id == user_id))).scalars()}
    out: list[FileRow] = []
    seen: dict[str, int] = {}
    for t in table.rows:
        row = FileRow(t.line, "", t.values)
        out.append(row)
        errors = []
        try:
            row.offer_id = parse_key(t.values.get("offer_id"))
        except CellError as exc:
            errors.append(str(exc))
            row.offer_id = cell_text(t.values.get("offer_id"))[:100]
        try:
            row.price = parse_money(t.values.get("new_price"))
        except CellError as exc:
            errors.append(f"новая цена: {exc}")
        if not row.offer_id and not errors:
            errors.append("не указан артикул")
        elif row.offer_id in seen:
            errors.append(f"артикул уже есть в строке {seen[row.offer_id]}")
        elif row.offer_id:
            seen[row.offer_id] = t.line
            row.product = products.get(row.offer_id)
            if row.product is None:
                errors.append("такого артикула нет среди ваших товаров Ozon — проверьте его или обновите товары")
        if row.price is not None:
            if row.price <= 0:
                errors.append("новая цена должна быть больше 0")
            elif row.price > MAX_PRICE:
                errors.append("новая цена больше 100 000 000 ₽ — проверьте число")
            elif row.price != row.price.quantize(CENT):
                errors.append("новая цена — в рублях и копейках: не больше двух знаков после запятой")
            else:
                row.price = row.price.quantize(CENT)
        if errors:
            text = "; ".join(errors)
            row.error = text[:1].upper() + text[1:]
    return out, table.format


async def _changed(db: AsyncSession, rows: list[FileRow]) -> list[FileRow]:
    """Строки без ошибок, где новая цена отличается от текущей (по последнему снимку)."""
    good = [r for r in rows if r.error is None and r.price is not None and r.product is not None]
    current = await latest_prices(db, [r.product.id for r in good])
    return [r for r in good if (s := current.get(r.product.id)) is None or positive(s.price) != r.price]


async def upload(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str, reason: str | None) -> dict:
    """Файл → тот же массовый предпросмотр. Файл без правок — 0 изменений."""
    rows, _ = await parse_file(db, user_id, content, filename)
    changed = await _changed(db, rows)
    errors = [{"line": r.line, "offer_id": r.offer_id, "message": r.error} for r in rows if r.error]
    info = {"rows": len(rows), "errors": errors, "unchanged": len(rows) - len(errors) - len(changed)}
    if not changed:
        now = _now()
        return {"batch_id": None, "created_at": now, "expires_at": now + PREVIEW_TTL, "description": None,
                "strategy": None, "price_fresh": True, "warnings": [],
                "counts": {"ok": 0, "warn": 0, "blocked": 0, "unchanged": info["unchanged"], "total": 0},
                "items": [], "file": info}
    if len(changed) > price_safety.MAX_PRODUCTS:
        raise PriceUpdateError(400, f"В файле {len(changed)} изменённых цен, а за один раз можно не больше "
                                    f"{price_safety.MAX_PRODUCTS}. Разделите файл на части.")
    result = await preview(db, user_id, reason=reason, file_info=info,
                           file_items=[Item(r.product, r.price, line=r.line) for r in changed])
    info["unchanged"] += result["counts"]["unchanged"]  # цена в Ozon уже такая (обновилась после выгрузки)
    return result


async def error_file(db: AsyncSession, user_id: uuid.UUID, content: bytes, filename: str) -> tuple[bytes, str]:
    """Строки с ошибками — тем же шаблоном с колонкой «Ошибка»: и ошибки разбора, и что не прошло проверки §8 (по
    последним данным)."""
    rows, fmt = await parse_file(db, user_id, content, filename)
    changed = await _changed(db, rows)
    if changed:
        checker = price_safety.PriceSafetyChecker()
        checks = await price_safety.gather(db, user_id, [price_safety.Proposal(r.product, r.price) for r in changed])
        for r, c in zip(changed, checks):
            errors = ([ARCHIVED] if r.product.is_archived else []) + checker.check(c).errors
            if errors:
                r.error = " ".join(errors)
    bad = [r for r in rows if r.error]
    if not bad:
        raise PriceUpdateError(400, "В файле нет строк с ошибками.")
    current = await latest_prices(db, [r.product.id for r in bad if r.product is not None])
    columns = [ERROR_COLUMN] + list(PRICE_SPEC.template_columns)
    out = []
    for r in bad:
        row = {c.key: echo(c.kind, r.raw.get(c.key)) for c in columns if not c.reference}
        snap = current.get(r.product.id) if r.product is not None else None
        row.update(_error=r.error, name=r.product.name if r.product else None,
                   price=positive(snap.price) if snap is not None else None)
        out.append(row)
    return build_file(fmt, PRICE_SPEC, out, columns), fmt
