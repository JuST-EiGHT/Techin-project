"""«Финансы → Долги поставщикам» (шаг 10.23a): поставки, продажи по ним, долг и оплаты.

Только для тех, кто платит поставщику после продажи («Откуда товар» = after_sale). Расчёт — engine/supplier_lots:
продажи из начислений Ozon (как P&L и `sales_daily`: отчёт XLSX за день, иначе API) относятся к поставкам по FIFO
внутри артикула, возвраты — к поставке своего отправления; цена — справочник себестоимости на дату поставки с НДС
поставщика; оплаты — статья «Оплата поставщику» в «Операциях» и оплаты, внесённые здесь.

Артикул документа сопоставляется с артикулом товара только по артикулу (правило владельца 14.11.2026): точное
совпадение без регистра и разделителей (`offer_match.normalize`) или привязка артикула «не из Ozon» (10.15).
Правило 12: поставки и оплаты — версии, ничего не перезаписывается. Коммит — на вызывающем.
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import sales as sales_engine
from app.engine import supplier_lots as eng
from app.engine.offer_match import normalize
from app.integrations.xlsx.common import norm
from app.models.offer_alias import OfferAlias
from app.models.planning import MoneyOperation
from app.models.product import Product
from app.models.supplier_lot import CLOSE_REASONS, COMMENT_MAX, SupplierDocument, SupplierLot, SupplierLotLine, SupplierPayment
from app.services import pnl_data, sales
from app.services import planning as planning_svc
from app.services import user_settings as settings_svc
from app.services.costs import CostHistory, load_cost_history
from app.services.planning import _clean
from app.services.sync.finance import today_msk
from app.services.tax_profile import supplier_vat_multiplier

POSTPAY = ("after_sale",)  # «Откуда товар», при котором раздел есть (решение владельца 14.11.2026 — пока только так)
SUPPLIER_CODE = "supplier"  # статья «Оплата поставщику»: её операции засчитываются как оплаты
ZERO = Decimal(0)
CLOSE_TITLES = {"returned": "вернули поставщику", "written_off": "списали", "other": "закрыта вручную"}
NOT_POSTPAY = (
    "Раздел «Долги поставщикам» — для тех, кто платит поставщику после продажи. Если это про вас, выберите в "
    "«Настройки → Откуда товар» вариант «Поставщик получает деньги после продажи»."
)


class PayablesError(Exception):
    """Запрос не подходит. Текст — для пользователя."""


class NotFound(PayablesError):
    pass


class NotPostpay(PayablesError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def require_postpay(db: AsyncSession, user_id: uuid.UUID):
    us = await settings_svc.get_settings(db, user_id)
    if us.supplier_payment not in POSTPAY:
        raise NotPostpay(NOT_POSTPAY)
    return us


# ---------- сопоставление артикулов ----------


@dataclass
class Catalog:
    offers: dict[str, str]  # ключ артикула → артикул товара (каталог Ozon и справочник себестоимости)
    in_catalog: set[str]  # ключи артикулов товаров каталога Ozon
    aliases: dict[str, str]  # ключ артикула «не из Ozon» → артикул товара (привязки 10.15)

    def key(self, article: str) -> str:
        k = normalize(article)
        if k not in self.offers and k in self.aliases:
            return normalize(self.aliases[k])
        return k

    def offer(self, article: str) -> str | None:
        return self.offers.get(self.key(article))


async def catalog(db: AsyncSession, user_id: uuid.UUID, costs: CostHistory) -> Catalog:
    products = [o for (o,) in await db.execute(select(Product.offer_id).where(Product.user_id == user_id))]
    offers = {normalize(o): o for o in costs.offers()}
    offers.update({normalize(o): o for o in products})  # артикул каталога Ozon важнее написания в справочнике
    aliases = {a.alias_key: a.offer_id for a in (await db.execute(select(OfferAlias).where(
        OfferAlias.user_id == user_id, OfferAlias.removed_at.is_(None)))).scalars()}
    return Catalog(offers, {normalize(o) for o in products}, aliases)


async def price_of(db: AsyncSession, user_id: uuid.UUID, costs: CostHistory, offer: str | None, day: date,
                   vat_cache: dict[date, Decimal]) -> Decimal | None:
    """Цена штуки для поставщика: себестоимость на дату поставки × НДС поставщика (свой у записи, иначе из профиля)."""
    if offer is None:
        return None
    rec = costs.record_at(offer, day)
    if rec is None:
        return None
    if rec.vat_multiplier is not None:
        vat = Decimal(rec.vat_multiplier)
    else:
        if day not in vat_cache:
            vat_cache[day] = await supplier_vat_multiplier(db, user_id, day)
        vat = vat_cache[day]
    return Decimal(rec.cost_price) * vat


# ---------- поставки ----------


def _current(q):
    return q.where(SupplierLot.superseded_at.is_(None), SupplierLot.deleted_at.is_(None))


async def lots(db: AsyncSession, user_id: uuid.UUID) -> list[SupplierLot]:
    q = _current(select(SupplierLot).where(SupplierLot.user_id == user_id))
    return list((await db.execute(q.order_by(SupplierLot.day, SupplierLot.number))).scalars())


async def lines_of(db: AsyncSession, rows: list[SupplierLot]) -> dict[uuid.UUID, list[SupplierLotLine]]:
    out: dict[uuid.UUID, list[SupplierLotLine]] = defaultdict(list)
    if not rows:
        return out
    q = select(SupplierLotLine).where(SupplierLotLine.lot_row_id.in_([r.id for r in rows]))
    for ln in (await db.execute(q)).scalars():
        out[ln.lot_row_id].append(ln)
    for lst in out.values():
        lst.sort(key=lambda ln: (ln.article, ln.id))
    return out


async def current_lot(db: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID) -> SupplierLot:
    row = (await db.execute(_current(select(SupplierLot).where(
        SupplierLot.user_id == user_id, SupplierLot.lot_id == lot_id)))).scalar_one_or_none()
    if row is None:
        raise NotFound("Поставка не найдена")
    return row


def lot_key(supplier: str | None, number: str, day: date) -> tuple[str, str, date]:
    """Одна и та же поставка: тот же поставщик, № и дата документа."""
    return (norm(supplier or ""), norm(number), day)


async def new_version(db: AsyncSession, row: SupplierLot, lines: list[dict], **changes) -> SupplierLot:
    """Новая версия поставки с этими строками; прежняя остаётся в истории."""
    row.superseded_at = _now()
    fields = ("number", "day", "supplier", "receiver", "warehouse", "doc_total_qty", "source", "document_id", "comment",
              "closed_on", "close_reason")
    new = SupplierLot(lot_id=row.lot_id, version=row.version + 1, user_id=row.user_id,
                      **{f: changes.get(f, getattr(row, f)) for f in fields})
    db.add(new)
    await db.flush()
    for ln in lines:
        db.add(SupplierLotLine(lot_row_id=new.id, **ln))
    return new


def _line_dict(ln: SupplierLotLine, **changes) -> dict:
    base = {"article": ln.article, "name": ln.name, "barcode": ln.barcode, "qty": ln.qty, "sold_before": ln.sold_before,
            "written_off": ln.written_off}
    return {**base, **changes}


async def update_lot(db: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID, comment=None,
                     sold_before: dict[str, int] | None = None, set_comment: bool = False) -> SupplierLot:
    """Описание и «уже продано ранее» по артикулам → новая версия."""
    await require_postpay(db, user_id)
    row = await current_lot(db, user_id, lot_id)
    lines = (await lines_of(db, [row]))[row.id]
    sold_before = sold_before or {}
    known = {ln.article for ln in lines}
    if unknown := [a for a in sold_before if a not in known]:
        raise PayablesError(f"В поставке нет артикула «{unknown[0]}»")
    new_lines = []
    for ln in lines:
        n = sold_before.get(ln.article, ln.sold_before)
        if n is None or n < 0 or n > ln.qty:
            raise PayablesError(f"«Уже продано ранее» у {ln.article} — от 0 до {ln.qty} шт.")
        new_lines.append(_line_dict(ln, sold_before=n))
    new_comment = _clean(comment, COMMENT_MAX) if set_comment else row.comment
    if new_comment == row.comment and all(a["sold_before"] == b.sold_before for a, b in zip(new_lines, lines)):
        return row
    return await new_version(db, row, new_lines, comment=new_comment)


async def close_lot(db: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID, reason: str, comment: str | None) -> SupplierLot:
    """Закрыть поставку с остатком (вернули поставщику, списали): остаток уходит в «списано», новых продаж она не берёт."""
    if reason not in CLOSE_REASONS:
        raise PayablesError("Причина закрытия: вернули поставщику, списали или другое")
    row = await current_lot(db, user_id, lot_id)
    if row.closed_on is not None:
        raise PayablesError("Поставка уже закрыта")
    result = (await compute(db, user_id)).result
    out = next(o for o in result.lots if o.lot.lot_id == str(lot_id))
    lines = (await lines_of(db, [row]))[row.id]  # тот же порядок строк, что в расчёте
    new_lines = [_line_dict(ln, written_off=ln.written_off + max(o.remaining, 0)) for ln, o in zip(lines, out.lines)]
    text = _clean(comment, COMMENT_MAX) or row.comment
    return await new_version(db, row, new_lines, closed_on=today_msk(), close_reason=reason, comment=text)


async def reopen_lot(db: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID) -> SupplierLot:
    row = await current_lot(db, user_id, lot_id)
    if row.closed_on is None:
        raise PayablesError("Поставка не закрыта")
    lines = (await lines_of(db, [row]))[row.id]
    return await new_version(db, row, [_line_dict(ln, written_off=0) for ln in lines], closed_on=None, close_reason=None)


async def delete_lot(db: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID) -> None:
    row = await current_lot(db, user_id, lot_id)
    row.deleted_at = _now()


async def document(db: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID) -> SupplierDocument:
    row = await current_lot(db, user_id, lot_id)
    doc = await db.get(SupplierDocument, row.document_id) if row.document_id else None
    if doc is None or doc.user_id != user_id:
        raise NotFound("У поставки нет загруженного документа")
    return doc


# ---------- оплаты, внесённые здесь ----------


def _pay_current(q):
    return q.where(SupplierPayment.superseded_at.is_(None), SupplierPayment.deleted_at.is_(None))


async def payments(db: AsyncSession, user_id: uuid.UUID) -> list[SupplierPayment]:
    q = _pay_current(select(SupplierPayment).where(SupplierPayment.user_id == user_id))
    return list((await db.execute(q.order_by(SupplierPayment.day.desc(), SupplierPayment.created_at.desc()))).scalars())


async def current_payment(db: AsyncSession, user_id: uuid.UUID, payment_id: uuid.UUID) -> SupplierPayment:
    row = (await db.execute(_pay_current(select(SupplierPayment).where(
        SupplierPayment.user_id == user_id, SupplierPayment.payment_id == payment_id)))).scalar_one_or_none()
    if row is None:
        raise NotFound("Оплата не найдена")
    return row


PAY_FIELDS = ("day", "amount", "counterparty", "comment")


def check_payment(data: dict) -> dict:
    today = today_msk()
    day = data.get("day")
    if day is None or day < planning_svc.MIN_DAY:
        raise PayablesError("Дата оплаты: укажите дату — не раньше 2000 года")
    if day > today:
        raise PayablesError("Дата оплаты — не позже сегодняшнего дня")
    amount = data.get("amount")
    if amount is None or amount <= 0:
        raise PayablesError("Сумма оплаты — больше нуля")
    return {"day": day, "amount": planning_svc._check_amount(amount), "counterparty": _clean(data.get("counterparty"), 200),
            "comment": _clean(data.get("comment"), COMMENT_MAX)}


async def add_payment(db: AsyncSession, user_id: uuid.UUID, data: dict) -> SupplierPayment:
    await require_postpay(db, user_id)
    row = SupplierPayment(payment_id=uuid.uuid4(), version=1, user_id=user_id, **check_payment(data))
    db.add(row)
    return row


async def update_payment(db: AsyncSession, user_id: uuid.UUID, payment_id: uuid.UUID, changes: dict) -> SupplierPayment:
    row = await current_payment(db, user_id, payment_id)
    clean = check_payment({k: changes.get(k, getattr(row, k)) for k in PAY_FIELDS})
    if all(clean[k] == getattr(row, k) for k in PAY_FIELDS):
        return row
    row.superseded_at = _now()
    new = SupplierPayment(payment_id=row.payment_id, version=row.version + 1, user_id=user_id, **clean)
    db.add(new)
    return new


async def delete_payment(db: AsyncSession, user_id: uuid.UUID, payment_id: uuid.UUID) -> None:
    row = await current_payment(db, user_id, payment_id)
    row.deleted_at = _now()


async def operation_payments(db: AsyncSession, user_id: uuid.UUID, start: date, today: date) -> list[MoneyOperation]:
    """Операции статьи «Оплата поставщику» (с подстатьями) с даты первой поставки: расход — оплата, приход — возврат денег
    от поставщика; переводы между своими счетами — не оплата."""
    cats = await planning_svc._categories(db, user_id)
    root = {c.id for c in cats if c.code == SUPPLIER_CODE}
    ids = root | {c.id for c in cats if c.parent_id in root}
    if not ids:
        return []
    q = planning_svc._current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.category_id.in_(ids), MoneyOperation.transfer_id.is_(None),
        MoneyOperation.day >= start, MoneyOperation.day <= today,
    )).order_by(MoneyOperation.day.desc(), MoneyOperation.created_at.desc())
    return list((await db.execute(q)).scalars())


# ---------- продажи из начислений ----------


async def sale_events(db: AsyncSession, user_id: uuid.UUID, start: date, end: date) -> tuple[list[eng.Event], list[date], dict[str, str]]:
    """Продажи и возвраты после получения по отправлениям, (события, дни без данных, ключ → артикул Ozon)."""
    if start > end:
        return [], [], {}
    cov = await pnl_data.coverage(db, user_id, start, end, "auto")
    lines = await pnl_data.load_lines(db, user_id, cov)
    numbers = {ln.posting_number for ln in lines if not ln.offer_id and ln.posting_number}
    posting_offers = await pnl_data.posting_offers(db, user_id, numbers)
    events, names = [], {}
    for i, ln in enumerate(lines):
        kind = norm(ln.type_name)
        if kind not in (sales_engine.REVENUE, sales_engine.RETURN):
            continue
        offer = ln.offer_id
        if not offer and ln.posting_number and len(offers := posting_offers.get(ln.posting_number, set())) == 1:
            offer = next(iter(offers))
        if not offer:
            continue
        key = normalize(offer)
        names.setdefault(key, offer)
        qty = abs(ln.quantity) if ln.quantity else 1  # как в отчёте: пусто или 0 — одна штука
        events.append(eng.Event(ln.day, key, qty, "sale" if kind == sales_engine.REVENUE else "return",
                                ln.posting_number, i))
    missing = sorted(d for d, s in cov.days.items() if s is None)
    return events, missing, names


# ---------- расчёт ----------


@dataclass
class Computed:
    result: eng.Result
    rows: list[SupplierLot]
    lines: dict[uuid.UUID, list[SupplierLotLine]]
    catalog: Catalog
    missing_days: list[date]
    names: dict[str, str]
    manual: list[SupplierPayment]
    ops: list[MoneyOperation]
    start: date | None
    today: date


async def compute(db: AsyncSession, user_id: uuid.UUID) -> Computed:
    today = today_msk()
    rows = await lots(db, user_id)
    lines = await lines_of(db, rows)
    costs = await load_cost_history(db, user_id)
    cat = await catalog(db, user_id, costs)
    start = min((r.day for r in rows), default=None)
    vat_cache: dict[date, Decimal] = {}
    lots_in = []
    for r in rows:
        items = []
        for ln in lines[r.id]:
            offer = cat.offer(ln.article)
            price = await price_of(db, user_id, costs, offer, r.day, vat_cache)
            items.append(eng.LineIn(cat.key(ln.article), ln.article, ln.qty, ln.sold_before, ln.written_off, price))
        lots_in.append(eng.LotIn(str(r.lot_id), r.day, r.number, tuple(items), closed_on=r.closed_on))
    events, missing, names = await sale_events(db, user_id, start, today) if start else ([], [], {})
    manual = [p for p in await payments(db, user_id) if start and p.day >= start]
    ops = await operation_payments(db, user_id, start, today) if start else []
    pays = [eng.Payment(p.day, p.amount) for p in manual] + [eng.Payment(o.day, -o.amount) for o in ops]
    result = eng.allocate(lots_in, events, pays)
    return Computed(result, rows, lines, cat, missing, names, manual, ops, start, today)


def _days_text(days: list[date]) -> str:
    if len(days) == 1:
        return f"{days[0]:%d.%m.%Y}"
    if (days[-1] - days[0]).days + 1 == len(days):
        return f"с {days[0]:%d.%m.%Y} по {days[-1]:%d.%m.%Y}"
    return f"{days[0]:%d.%m}–{days[-1]:%d.%m}, не подряд"


async def state(db: AsyncSession, user_id: uuid.UUID, brief: bool = False) -> dict:
    await require_postpay(db, user_id)
    c = await compute(db, user_id)
    res = c.result
    by_id = {str(r.lot_id): r for r in c.rows}
    last_30 = c.today - timedelta(days=29)
    paid_30 = sum((p.amount for p in c.manual if p.day >= last_30), ZERO) + sum(
        (-o.amount for o in c.ops if o.day >= last_30), ZERO)
    open_lots = [o for o in res.lots if o.status != eng.CLOSED]
    stock_value = eng.q(sum((ln.stock_value or ZERO for o in open_lots for ln in o.lines), ZERO))
    stock_unknown = sum(max(ln.remaining, 0) for o in open_lots for ln in o.lines if ln.line.price is None)
    counts = defaultdict(int)
    for o in res.lots:
        counts[o.status] += 1
    unknown_sold = sum(o.unknown_price for o in res.lots)
    warnings = []
    if c.missing_days:
        warnings.append(
            f"Нет данных о продажах за {len(c.missing_days)} дн. ({_days_text(c.missing_days)}) — продажи этих дней в "
            "поставки не попали: день без данных — не день без продаж. Обновите начисления («Синхронизация») или "
            "загрузите отчёт Ozon за эти дни."
        )
    if unknown_sold:
        warnings.append(
            f"Продано {unknown_sold} шт. без себестоимости — долг по ним неизвестен и в сумму не вошёл. Заполните "
            "себестоимость в «Товары → Себестоимость» — цена возьмётся на дату поставки."
        )
    if res.unallocated:
        qty = sum(res.unallocated.values())
        warnings.append(
            f"Продано {qty} шт. ({len(res.unallocated)} арт.), для которых в поставках нет остатка: загрузите поставку, "
            "из которой они проданы, или укажите «уже продано ранее» у старой поставки."
        )
    if res.orphan_returns:
        warnings.append(
            f"Возвратов {sum(res.orphan_returns.values())} шт. не к чему отнести — их продажа была до учёта поставок."
        )
    if res.prepaid > 0:
        warnings.append(
            f"Оплачено больше, чем начислено по проданному: аванс поставщику {_rub(res.prepaid)}. Он закроет следующие "
            "продажи."
        )
    summary = {
        "to_pay": res.to_pay, "accrued": res.accrued, "paid_total": res.paid_total, "paid_30d": eng.q(paid_30),
        "prepaid": res.prepaid, "stock_qty": sum(o.remaining for o in open_lots), "stock_value": stock_value,
        "stock_unknown_qty": stock_unknown, "unknown_price_qty": unknown_sold,
        "lots_total": len(res.lots), "lots_selling": counts[eng.SELLING], "lots_awaiting": counts[eng.AWAITING],
        "lots_done": counts[eng.DONE], "lots_closed": counts[eng.CLOSED],
        "unallocated_qty": sum(res.unallocated.values()),
    }
    rng = await sales_range(db, user_id)
    out = {"enabled": True, "today": c.today, "configured": bool(c.rows), "summary": summary, "warnings": warnings,
           "sales_known_from": rng, "operations_counted": bool(c.ops), "lots": [], "payments": [], "unallocated": []}
    if brief:
        return out
    out["lots"] = [lot_out(o, by_id[o.lot.lot_id], c) for o in reversed(res.lots)]
    out["payments"] = sorted(
        [{"payment_id": p.payment_id, "day": p.day, "amount": p.amount, "counterparty": p.counterparty,
          "comment": p.comment, "source": "manual", "editable": True, "version": p.version} for p in c.manual]
        + [{"payment_id": o.op_id, "day": o.day, "amount": -o.amount, "counterparty": o.counterparty, "comment": o.comment,
            "source": "operation", "editable": False, "version": o.version} for o in c.ops[:500]],
        key=lambda p: p["day"], reverse=True,
    )
    out["unallocated"] = [{"article": c.names.get(k, k), "qty": v} for k, v in sorted(res.unallocated.items())]
    return out


async def sales_range(db: AsyncSession, user_id: uuid.UUID) -> date | None:
    """С какого дня сервис видит продажи: старым поставкам — «уже продано ранее» за то, что было до него."""
    rng = await sales.data_range(db, user_id)
    return rng[0] if rng else None


def _rub(v: Decimal) -> str:
    return f"{v:,.2f} ₽".replace(",", " ").replace(".", ",")


def lot_out(o: eng.LotOut, row: SupplierLot, c: Computed) -> dict:
    lines = c.lines[row.id]
    moves: dict[tuple, int] = defaultdict(int)
    for m in o.moves:
        moves[(m.day, m.key, "sale" if m.qty > 0 else "return")] += abs(m.qty)
    articles = {normalize(ln.article): ln.article for ln in lines}
    return {
        "lot_id": row.lot_id, "version": row.version, "number": row.number, "day": row.day, "supplier": row.supplier,
        "receiver": row.receiver, "warehouse": row.warehouse, "source": row.source, "doc_total_qty": row.doc_total_qty,
        "has_document": row.document_id is not None, "comment": row.comment, "closed_on": row.closed_on,
        "close_reason": row.close_reason, "status": o.status, "qty": o.qty, "sold": o.sold, "remaining": o.remaining,
        "debt": o.debt, "paid": o.paid, "unpaid": o.unpaid, "unknown_price_qty": o.unknown_price,
        "stock_value": eng.q(sum((ln.stock_value or ZERO for ln in o.lines), ZERO)),
        "lines": [
            {"article": ln.article, "name": ln.name, "barcode": ln.barcode, "offer": c.catalog.offer(ln.article),
             "in_catalog": c.catalog.key(ln.article) in c.catalog.in_catalog, "qty": ln.qty,
             "sold_before": ln.sold_before, "sold": lo.sold, "returned": lo.returned, "written_off": ln.written_off,
             "remaining": lo.remaining, "price": None if lo.line.price is None else eng.q(lo.line.price),
             "amount": lo.amount}
            for ln, lo in zip(lines, o.lines)
        ],
        "moves": [
            {"day": d, "article": articles.get(k, c.names.get(k, k)), "kind": kind, "qty": n}
            for (d, k, kind), n in sorted(moves.items(), key=lambda x: (x[0][0], x[0][1], x[0][2]), reverse=True)
        ],
    }
