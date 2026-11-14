"""Кредиторская задолженность перед поставщиком (шаг 10.29, «Финансы → Долги поставщикам»).

Только для тех, кто платит поставщику после продажи или с отсрочкой («Откуда товар»: after_sale, deferred, mixed).
Долг складывается из: долга на начало учёта (его вводит пользователь — история до сервиса неизвестна, «нет данных ≠ 0»),
продаж (после продажи: себестоимость проданного с НДС — те же цифры, что в P&L и в платёжном календаре) и закупок с
отсрочкой (вносит пользователь). Оплаты — статья «Оплата поставщику» в «Операциях» (если ведёте) и оплаты, внесённые здесь.

Правило 12: записи `supplier_entries` не перезаписываются — исправление новой версией, удаление — `deleted_at`.
Правило 13: поставщик, график и отсрочка — настройки пользователя, в коде ничего не зашито. Коммит — на вызывающем.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payables as eng
from app.models.planning import MoneyOperation, PlanningSettings
from app.models.supplier_entry import COMMENT_MAX, SupplierEntry
from app.services import plan_calendar
from app.services import planning as planning_svc
from app.services import user_settings as settings_svc
from app.services.planning import _clean
from app.services.sync.finance import today_msk
from app.services.tax_profile import supplier_vat_multiplier

POSTPAY = ("after_sale", "deferred", "mixed")  # «Откуда товар», при которых раздел доступен
SALES_MODE = ("after_sale",)  # долг по продажам считается сам — только когда поставщик получает деньги после продажи
SERIES_DAYS = 90
ZERO = Decimal(0)
WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
SUPPLIER_CODE = "supplier"  # статья «Оплата поставщику» — её операции засчитываются как оплаты
NOT_POSTPAY = (
    "Раздел «Долги поставщикам» — для тех, кто платит поставщику после продажи или с отсрочкой. Если это про вас, "
    "выберите в «Настройки → Откуда товар» нужный вариант."
)


class PayablesError(Exception):
    """Запрос не подходит. Текст — для пользователя."""


class NotFound(PayablesError):
    pass


class NotPostpay(PayablesError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mode_of(supplier_payment: str) -> str | None:
    """sales — долг считается по продажам, purchases — по закупкам, которые вносит пользователь; None — раздела нет."""
    if supplier_payment not in POSTPAY:
        return None
    return "sales" if supplier_payment in SALES_MODE else "purchases"


async def require_postpay(db: AsyncSession, user_id: uuid.UUID):
    us = await settings_svc.get_settings(db, user_id)
    if us.supplier_payment not in POSTPAY:
        raise NotPostpay(NOT_POSTPAY)
    return us


# ---------- записи ----------


def _current(q):
    return q.where(SupplierEntry.superseded_at.is_(None), SupplierEntry.deleted_at.is_(None))


async def entries(db: AsyncSession, user_id: uuid.UUID, kind: str | None = None) -> list[SupplierEntry]:
    q = select(SupplierEntry).where(SupplierEntry.user_id == user_id)
    if kind:
        q = q.where(SupplierEntry.kind == kind)
    return list((await db.execute(_current(q).order_by(SupplierEntry.day.desc(), SupplierEntry.created_at.desc()))).scalars())


async def opening(db: AsyncSession, user_id: uuid.UUID) -> SupplierEntry | None:
    rows = await entries(db, user_id, "opening")
    return rows[0] if rows else None


async def current_entry(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> SupplierEntry:
    row = (await db.execute(_current(select(SupplierEntry).where(
        SupplierEntry.user_id == user_id, SupplierEntry.entry_id == entry_id
    )))).scalar_one_or_none()
    if row is None:
        raise NotFound("Запись не найдена")
    return row


def _day(value: date | None, today: date, what: str) -> date:
    if value is None or value < planning_svc.MIN_DAY:
        raise PayablesError(f"{what}: укажите дату — не раньше 2000 года")
    return value


async def set_opening(
    db: AsyncSession, user_id: uuid.UUID, start: date, amount: Decimal, due_day: date | None = None
) -> SupplierEntry:
    """Начало учёта: с этого дня долг считается сам, `amount` — долг на начало дня (0 — долга не было). Повторно —
    новая версия: прежняя остаётся в истории."""
    await require_postpay(db, user_id)
    today = today_msk()
    start = _day(start, today, "Начало учёта")
    if start > today:
        raise PayablesError("Начало учёта — не позже сегодняшнего дня")
    if amount is None or amount < 0:
        raise PayablesError("Долг на начало учёта — сумма от нуля (0, если долга не было)")
    amount = planning_svc._check_amount(amount, "Долг")
    if due_day is not None and due_day < start:
        raise PayablesError("Срок оплаты начального долга раньше начала учёта")
    prev = await opening(db, user_id)
    if prev is not None:
        prev.superseded_at = _now()
    row = SupplierEntry(
        entry_id=prev.entry_id if prev else uuid.uuid4(), version=prev.version + 1 if prev else 1, user_id=user_id,
        kind="opening", day=start, amount=amount, due_day=due_day,
    )
    db.add(row)
    return row


FIELDS = ("day", "amount", "due_day", "number", "counterparty", "comment")


async def check_entry(
    db: AsyncSession, user_id: uuid.UUID, kind: str, data: dict, us=None
) -> dict:
    """Проверить и привести поля закупки или оплаты."""
    us = us or await require_postpay(db, user_id)
    if kind not in ("purchase", "payment"):
        raise PayablesError("Запись — закупка с отсрочкой или оплата поставщику")
    if kind == "purchase" and mode_of(us.supplier_payment) == "sales":
        raise PayablesError(
            "При оплате после продажи закупки не вносятся — долг считается по продажам сам. "
            "Закупка с отсрочкой — в «Настройки → Откуда товар»."
        )
    start_row = await opening(db, user_id)
    if start_row is None:
        raise PayablesError("Сначала укажите начало учёта и долг на эту дату — от него считается задолженность")
    today = today_msk()
    what = "Дата закупки" if kind == "purchase" else "Дата оплаты"
    day = _day(data.get("day"), today, what)
    if day > today:
        raise PayablesError(f"{what} — не позже сегодняшнего дня")
    if day < start_row.day:
        raise PayablesError(
            f"{what} раньше начала учёта ({start_row.day:%d.%m.%Y}): то, что было до этого дня, уже входит в начальный долг"
        )
    amount = data.get("amount")
    if amount is None or amount <= 0:
        raise PayablesError("Сумма — больше нуля")
    amount = planning_svc._check_amount(amount)
    due = data.get("due_day")
    if kind == "purchase":
        if due is None:
            days = us.supplier_deferral_days
            if days is None:
                raise PayablesError("Укажите срок оплаты — отсрочка по умолчанию в настройках не задана")
            due = day + timedelta(days=days)
        if due < day:
            raise PayablesError("Срок оплаты раньше даты закупки")
    else:
        due = None
    number = _clean(data.get("number"), 100)
    counterparty = _clean(data.get("counterparty"), 200)
    comment = _clean(data.get("comment"), COMMENT_MAX)
    return {"day": day, "amount": amount, "due_day": due, "number": number, "counterparty": counterparty,
            "comment": comment}


async def create_entry(db: AsyncSession, user_id: uuid.UUID, kind: str, data: dict, source: str = "manual") -> SupplierEntry:
    clean = await check_entry(db, user_id, kind, data)
    row = SupplierEntry(entry_id=uuid.uuid4(), version=1, user_id=user_id, kind=kind, source=source, **clean)
    db.add(row)
    return row


def _same(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return a is not None and b is not None and Decimal(a) == Decimal(b)
    return a == b


async def update_entry(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID, changes: dict) -> tuple[SupplierEntry, bool]:
    """Изменилось хоть что-то — новая версия, прежняя остаётся в истории. Начальный долг меняется через set_opening."""
    row = await current_entry(db, user_id, entry_id)
    if row.kind == "opening":
        raise PayablesError("Начало учёта меняется в блоке «Начало учёта»")
    data = {k: changes.get(k, getattr(row, k)) for k in FIELDS}
    clean = await check_entry(db, user_id, row.kind, data)
    if all(_same(clean[k], getattr(row, k)) for k in FIELDS):
        return row, False
    row.superseded_at = _now()
    new = SupplierEntry(entry_id=row.entry_id, version=row.version + 1, user_id=user_id, kind=row.kind,
                        source=row.source, **clean)
    db.add(new)
    return new, True


async def delete_entry(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
    row = await current_entry(db, user_id, entry_id)
    if row.kind == "opening":
        raise PayablesError("Начало учёта нельзя удалить — его можно только изменить: так история остаётся")
    row.deleted_at = _now()


# ---------- откуда берутся начисления и оплаты ----------


def schedule_of(s: PlanningSettings | None) -> tuple[str, int]:
    """График оплаты поставщику (период, день) — тот же, что у платежей в календаре; нет настройки — раз в неделю, пн."""
    return (s.supplier_period, s.supplier_day) if s else ("week", 1)


def schedule_title(period: str, day: int) -> str:
    if period == "month":
        return f"раз в месяц, {day}-го числа" if day < 31 else "раз в месяц, в последний день"
    return f"раз в неделю, в {WEEKDAYS[day - 1]}"


async def sale_accruals(
    db: AsyncSession, user_id: uuid.UUID, start: date, today: date, period: str, pay_day: int
) -> tuple[list[eng.Accrual], list[eng.Accrual], list[date], int]:
    """Долг по продажам с `start` по вчера. (по периодам оплаты, по дням, дни без данных, штук без себестоимости).

    Сегодняшний день не берём: начисления за него ещё неполные. День без данных не равен дню без продаж — он не входит в
    долг и называется в предупреждении."""
    end = today - timedelta(days=1)
    if start > end:
        return [], [], [], 0
    ctx = await plan_calendar.context(db, user_id)
    mult = await supplier_vat_multiplier(db, user_id)
    fig = await plan_calendar.figures(ctx, start, end, mult)
    daily = fig.daily_owed if fig else {}
    data_days = fig.data_days if fig else set()
    by_day: list[eng.Accrual] = []
    groups: dict[tuple[date, date, date], Decimal] = {}
    gaps: list[date] = []
    day = start
    while day <= end:
        if day not in data_days:
            gaps.append(day)
        elif (amount := daily.get(day, ZERO)) > 0:
            p_start, p_end, due = eng.sale_period(day, period, pay_day)
            by_day.append(eng.Accrual(day, due, eng.q(amount), "sale", "Продажи", period=(p_start, p_end)))
            groups[(p_start, p_end, due)] = groups.get((p_start, p_end, due), ZERO) + amount
        day += timedelta(days=1)
    grouped = [
        eng.Accrual(max(p_start, start), due, eng.q(total), "sale", sale_label(p_start, p_end, period), period=(p_start, p_end))
        for (p_start, p_end, due), total in sorted(groups.items())
    ]
    missing = sum((m.qty for m in fig.data.missing_cost), 0) if fig else 0
    return grouped, by_day, gaps, int(missing)


def sale_label(start: date, end: date, period: str) -> str:
    if period == "month":
        return f"Продажи за {plan_calendar.MONTHS[start.month - 1]} {start.year}"
    return f"Продажи {start:%d.%m}–{end:%d.%m}"


async def operation_payments(db: AsyncSession, user_id: uuid.UUID, start: date, today: date) -> list[MoneyOperation]:
    """Операции статьи «Оплата поставщику» (и её подстатей) с начала учёта — они засчитываются как оплаты сами.
    Расход — оплата; приход (возврат от поставщика) — минус к оплатам; переводы между счетами — не оплата."""
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


# ---------- состояние страницы ----------


def _entry_out(e: SupplierEntry) -> dict:
    return {"entry_id": e.entry_id, "kind": e.kind, "day": e.day, "amount": e.amount, "due_day": e.due_day,
            "number": e.number, "counterparty": e.counterparty, "comment": e.comment, "source": e.source,
            "editable": e.kind != "opening", "version": e.version}


def _op_out(op: MoneyOperation) -> dict:
    return {"entry_id": op.op_id, "kind": "payment", "day": op.day, "amount": -op.amount, "due_day": None, "number": None,
            "counterparty": op.counterparty, "comment": op.comment, "source": "operation", "editable": False, "version": 1}


def _money(v: Decimal) -> Decimal:
    return eng.q(v)


async def state(db: AsyncSession, user_id: uuid.UUID, brief: bool = False) -> dict:
    """Всё, что показывает страница. `brief` — только итоги (карточка в «Обзоре финансов»)."""
    us = await require_postpay(db, user_id)
    mode = mode_of(us.supplier_payment)
    today = today_msk()
    ps = await db.get(PlanningSettings, user_id)
    period, pay_day = schedule_of(ps)
    base = {
        "enabled": True, "supplier_payment": us.supplier_payment, "mode": mode, "configured": False, "today": today,
        "deferral_days": us.supplier_deferral_days, "start": None, "opening_amount": None, "opening_due": None,
        "schedule": {"period": period, "day": pay_day, "title": schedule_title(period, pay_day)} if mode == "sales" else None,
        "summary": None, "buckets": None, "items": [], "series": [], "entries": [], "warnings": [], "incomplete": False,
        "operations_counted": False,
    }
    start_row = await opening(db, user_id)
    if start_row is None:
        return base
    start = start_row.day

    accruals: list[eng.Accrual] = [
        eng.Accrual(start, start_row.due_day, start_row.amount, "opening", "Долг на начало учёта")
    ] if start_row.amount > 0 else []
    daily_accruals = list(accruals)
    warnings: list[str] = []
    incomplete = False
    if mode == "sales":
        grouped, by_day, gaps, missing = await sale_accruals(db, user_id, start, today, period, pay_day)
        accruals += grouped
        daily_accruals += by_day
        if gaps:
            incomplete = True
            warnings.append(
                f"Нет данных о продажах за {len(gaps)} дн. ({_gap_text(gaps)}) — долг по ним не учтён, а день без данных не "
                "считаем днём без продаж. Загрузите отчёт Ozon за эти дни или обновите начисления («Синхронизация»)."
            )
        if missing:
            incomplete = True
            warnings.append(
                f"У проданных товаров нет себестоимости: {missing} шт. — долг по ним не посчитан и занижен. "
                "Заполните её в «Товары → Себестоимость»."
            )
    purchases = [e for e in await entries(db, user_id, "purchase") if start <= e.day <= today]
    for e in purchases:
        label = f"Закупка № {e.number}" if e.number else "Закупка"
        item = eng.Accrual(e.day, e.due_day, e.amount, "purchase", label, ref=str(e.entry_id))
        accruals.append(item)
        daily_accruals.append(item)
    manual = [e for e in await entries(db, user_id, "payment") if start <= e.day <= today]
    ops = await operation_payments(db, user_id, start, today)
    payments = [eng.Payment(e.day, e.amount, "manual", str(e.entry_id)) for e in manual] + [
        eng.Payment(o.day, -o.amount, "operation", str(o.op_id)) for o in ops
    ]
    older = [e for e in await entries(db, user_id) if e.kind != "opening" and (e.day < start or e.day > today)]
    if older:
        warnings.append(f"Записей раньше начала учёта: {len(older)} — они не считаются: до этого дня долг уже в начальной сумме.")

    ledger = eng.settle(accruals, payments, today)
    last_30 = today - timedelta(days=29)
    paid_30 = eng.q(sum((p.amount for p in payments if p.day >= last_30), ZERO))
    accrued_30 = eng.q(sum((a.amount for a in daily_accruals if a.kind != "opening" and a.day >= last_30), ZERO))
    if ledger.prepaid > 0:
        warnings.append(
            f"Оплачено больше, чем начислено: аванс поставщику {ledger.prepaid:,.2f} ₽. Проверьте начальный долг и оплаты — "
            "возможно, часть оплат относится к периоду до начала учёта.".replace(",", " ")
        )
    summary = {
        "debt": ledger.debt, "overdue": ledger.overdue, "not_due": ledger.not_due, "prepaid": ledger.prepaid,
        "accrued": ledger.accrued, "paid": ledger.paid, "due_7": ledger.due_in[7], "due_14": ledger.due_in[14],
        "due_30": ledger.due_in[30], "paid_30d": paid_30, "accrued_30d": accrued_30,
        "overdue_count": sum(1 for i in ledger.items if i.overdue),
        "oldest_overdue_days": max((i.overdue_days for i in ledger.items), default=0),
    }
    out = {**base, "configured": True, "start": start, "opening_amount": start_row.amount, "opening_due": start_row.due_day,
           "summary": summary, "buckets": ledger.buckets, "warnings": warnings, "incomplete": incomplete,
           "operations_counted": bool(ops)}
    if brief:
        return out
    out["items"] = [
        {"kind": i.accrual.kind, "label": i.accrual.label, "day": i.accrual.day, "due": i.accrual.due,
         "amount": i.accrual.amount, "remaining": i.remaining, "overdue_days": i.overdue_days, "ref": i.accrual.ref,
         "period_from": i.accrual.period[0] if i.accrual.period else None,
         "period_to": i.accrual.period[1] if i.accrual.period else None}
        for i in ledger.items
    ]
    series_from = max(start, today - timedelta(days=SERIES_DAYS - 1))
    out["series"] = [{"day": d, "debt": v} for d, v in eng.series(daily_accruals, payments, series_from, today)]
    rows = [_entry_out(e) for e in [*purchases, *manual]] + [_op_out(o) for o in ops[:500]]
    rows.sort(key=lambda r: r["day"], reverse=True)
    out["entries"] = rows
    return out


def _gap_text(gaps: list[date]) -> str:
    if len(gaps) == 1:
        return f"{gaps[0]:%d.%m.%Y}"
    return f"с {gaps[0]:%d.%m.%Y} по {gaps[-1]:%d.%m.%Y}" if (gaps[-1] - gaps[0]).days + 1 == len(gaps) else (
        f"{gaps[0]:%d.%m}–{gaps[-1]:%d.%m}, не все дни подряд"
    )
