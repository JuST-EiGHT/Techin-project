"""Плановые платежи (шаг 3.9): разовые и регулярные, с версиями; переопределения отдельных платежей.

Правило 12: изменение плана — новая версия (`plan_id` тот же, `version` + 1, прежняя — `superseded_at`), удаление —
`deleted_at`. Переопределение платежа (перенос, своя сумма, пропуск, «оплачено») — новая запись `planned_overrides`,
действует последняя по ключу. Коммит — на вызывающем.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payment_calendar as cal
from app.models.planning import COMMENT_MAX, PLAN_KINDS, PLAN_PERIODS, PlannedOverride, PlannedPayment
from app.services import planning as planning_svc
from app.services.planning import NotFound, PlanningError, _clean

MAX_MOVE_DAYS = 90  # перенести платёж можно не дальше чем на 90 дней от даты по плану
PERIOD_TITLES = {"once": "разово", "week": "раз в неделю", "month": "раз в месяц", "quarter": "раз в квартал",
                 "year": "раз в год"}
WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
KEY_PATTERNS = (
    re.compile(r"^plan:(?P<plan>[0-9a-f-]{36}):(?P<day>\d{4}-\d{2}-\d{2})$"),
    re.compile(r"^supplier:(?P<day>\d{4}-\d{2}-\d{2})$"),
    re.compile(r"^ozon:(?P<day>\d{4}-\d{2}-\d{2})$"),
    re.compile(r"^tax:[a-z_]+:[a-z_]+:(?P<day>\d{4}-\d{2}-\d{2}):[a-z0-9]+$"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- планы ----------


@dataclass
class PlanIn:
    kind: str  # income | expense
    amount: Decimal  # > 0
    start_date: date  # разовый — дата платежа; регулярный — с какой даты
    title: str | None = None  # пусто — по статье
    period: str = "once"
    day: int | None = None
    end_date: date | None = None
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    counterparty: str | None = None
    comment: str | None = None
    enabled: bool = True
    purchase_day: date | None = None  # закупка с отсрочкой: оплата = закупка + N дней


FIELDS = ("kind", "title", "amount", "category_id", "account_id", "counterparty", "comment", "period", "day",
          "start_date", "end_date", "purchase_day", "enabled")


def _current(q):
    return q.where(PlannedPayment.superseded_at.is_(None), PlannedPayment.deleted_at.is_(None))


async def check_plan(
    db: AsyncSession, user_id: uuid.UUID, data: PlanIn, keep: PlannedPayment | None = None
) -> PlanIn:
    """Проверить и привести поля. keep — текущая версия: её статья и счёт могут быть уже в архиве."""
    if data.kind not in PLAN_KINDS:
        raise PlanningError("Плановый платёж — поступление или расход")
    if data.amount is None or data.amount <= 0:
        raise PlanningError("Сумма — больше нуля")
    data.amount = planning_svc._check_amount(data.amount)
    if data.period not in PLAN_PERIODS:
        raise PlanningError("Периодичность: разово, раз в неделю, месяц, квартал или год")
    if data.start_date is None or data.start_date < planning_svc.MIN_DAY:
        raise PlanningError("Дата: укажите дату — не раньше 2000 года")
    if data.period == "once":
        data.day, data.end_date = None, None
    elif data.day is not None:
        if data.period == "week" and not 1 <= data.day <= 7:
            raise PlanningError("День недели — от 1 (понедельник) до 7 (воскресенье)")
        if data.period != "week" and not 1 <= data.day <= 31:
            raise PlanningError("Число месяца — от 1 до 31 (31 — последний день месяца)")
    if data.end_date is not None and data.end_date < data.start_date:
        raise PlanningError("Дата окончания раньше даты начала")
    if data.purchase_day is not None:
        if data.period != "once" or data.kind != "expense":
            raise PlanningError("Закупка с отсрочкой — разовый платёж поставщику")
    category = None
    if data.category_id is not None:
        category = await planning_svc._category(db, user_id, data.category_id)
        if category.kind == "transfer":
            raise PlanningError("Перевод между своими счетами не планируется — это не доход и не расход")
        if category.kind != data.kind:
            what = "поступлений" if category.kind == "income" else "расходов"
            raise PlanningError(f"Статья «{category.name}» — для {what}: выберите другую или поменяйте «Приход / Расход»")
        if category.archived_at is not None and (keep is None or keep.category_id != category.id):
            raise PlanningError(f"Статья «{category.name}» в архиве — выберите другую")
    if data.account_id is not None:
        await planning_svc._account(db, user_id, data.account_id,
                                    active=keep is None or keep.account_id != data.account_id)
    title = (data.title or "").strip()
    if not title:
        title = category.name if category else ("Поступление" if data.kind == "income" else "Платёж")
    if len(title) > 200:
        raise PlanningError("Название — не длиннее 200 символов")
    data.title = title
    data.counterparty = _clean(data.counterparty, 200)
    data.comment = _clean(data.comment, COMMENT_MAX)
    return data


async def create_plan(db: AsyncSession, user_id: uuid.UUID, data: PlanIn, source: str = "manual") -> PlannedPayment:
    data = await check_plan(db, user_id, data)
    row = PlannedPayment(
        plan_id=uuid.uuid4(), version=1, user_id=user_id, source="supplier" if data.purchase_day else source,
        **{k: getattr(data, k) for k in FIELDS},
    )
    db.add(row)
    return row


async def current_plan(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> PlannedPayment:
    row = (await db.execute(_current(select(PlannedPayment).where(
        PlannedPayment.user_id == user_id, PlannedPayment.plan_id == plan_id
    )))).scalar_one_or_none()
    if row is None:
        raise NotFound("Плановый платёж не найден")
    return row


def _same(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return a is not None and b is not None and Decimal(a) == Decimal(b)
    return a == b


async def update_plan(
    db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID, changes: dict
) -> tuple[PlannedPayment, bool]:
    """Изменилось хоть что-то (включая «включён») — новая версия, прежняя остаётся в истории."""
    row = await current_plan(db, user_id, plan_id)
    data = PlanIn(**{k: changes.get(k, getattr(row, k)) for k in FIELDS})
    data = await check_plan(db, user_id, data, keep=row)
    new = {k: getattr(data, k) for k in FIELDS}
    if all(_same(new[k], getattr(row, k)) for k in FIELDS):
        return row, False
    row.superseded_at = _now()
    version = PlannedPayment(
        plan_id=row.plan_id, version=row.version + 1, user_id=user_id,
        source="supplier" if data.purchase_day else ("import" if row.source == "import" else "manual"), **new,
    )
    db.add(version)
    return version, True


async def delete_plan(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> None:
    row = await current_plan(db, user_id, plan_id)
    row.deleted_at = _now()


async def plans(db: AsyncSession, user_id: uuid.UUID) -> list[PlannedPayment]:
    res = await db.execute(_current(select(PlannedPayment).where(PlannedPayment.user_id == user_id))
                           .order_by(PlannedPayment.start_date, PlannedPayment.created_at))
    return list(res.scalars())


async def history(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> list[PlannedPayment]:
    rows = list((await db.execute(select(PlannedPayment).where(
        PlannedPayment.user_id == user_id, PlannedPayment.plan_id == plan_id
    ).order_by(PlannedPayment.version.desc()))).scalars())
    if not rows:
        raise NotFound("Плановый платёж не найден")
    return rows


def rule(row: PlannedPayment, deferral_days: int | None) -> cal.Rule:
    """Правило повторения. Закупка с отсрочкой — оплата через N дней после закупки (N — из настроек сейчас)."""
    if row.purchase_day is not None:
        return cal.Rule(row.purchase_day + timedelta(days=deferral_days or 0))
    return cal.Rule(row.start_date, row.period, row.day, row.end_date)


def schedule_text(row: PlannedPayment, deferral_days: int | None) -> str:
    """«раз в месяц, 5-го числа, с 01.11.2026» — для списка планов."""
    if row.purchase_day is not None:
        pay = row.purchase_day + timedelta(days=deferral_days or 0)
        return f"закупка {row.purchase_day:%d.%m.%Y}, оплата {pay:%d.%m.%Y} (через {deferral_days or 0} дн.)"
    if row.period == "once":
        return f"разово, {row.start_date:%d.%m.%Y}"
    day = row.day or (row.start_date.isoweekday() if row.period == "week" else row.start_date.day)
    when = f"в {WEEKDAYS[day - 1]}" if row.period == "week" else ("последнего числа" if day == 31 else f"{day}-го числа")
    text = f"{PERIOD_TITLES[row.period]}, {when}, с {row.start_date:%d.%m.%Y}"
    return text + (f" по {row.end_date:%d.%m.%Y}" if row.end_date else "")


def next_day(row: PlannedPayment, deferral_days: int | None, today: date) -> date | None:
    days = cal.occurrences(rule(row, deferral_days), today, today + timedelta(days=800))
    return days[0] if days else None


# ---------- переопределения отдельных платежей ----------


def parse_key(key: str) -> tuple[str, date, uuid.UUID | None]:
    """Ключ платежа → (источник, дата в ключе, план). Ключ не из календаря — ошибка."""
    for pattern in KEY_PATTERNS:
        if m := pattern.match(key or ""):
            source = key.split(":", 1)[0]
            plan = uuid.UUID(m.group("plan")) if "plan" in m.groupdict() else None
            return ("manual" if source == "plan" else source), date.fromisoformat(m.group("day")), plan
    raise PlanningError("Платёж не найден — обновите страницу")


async def latest_overrides(db: AsyncSession, user_id: uuid.UUID) -> dict[str, PlannedOverride]:
    res = await db.execute(select(PlannedOverride).where(PlannedOverride.user_id == user_id)
                           .order_by(PlannedOverride.created_at, PlannedOverride.id))
    return {o.key: o for o in res.scalars()}  # последняя запись по ключу перекрывает


def to_engine(o: PlannedOverride) -> cal.Override:
    return cal.Override(
        day=o.day, amount=None if o.amount is None else Decimal(o.amount), skipped=o.skipped,
        op_id=str(o.op_id) if o.op_id else None, paid_day=o.paid_day,
        paid_amount=None if o.paid_amount is None else Decimal(o.paid_amount),
    )


OVERRIDE_FIELDS = ("day", "amount", "skipped", "op_id", "paid_day", "paid_amount")


async def set_override(
    db: AsyncSession, user_id: uuid.UUID, key: str, changes: dict, planned_day: date | None = None
) -> PlannedOverride:
    """Новая запись по ключу: прежние поля + изменения. day=None — вернуть дату по плану, amount=None — сумму по
    плану. planned_day — дата по плану (для проверки переноса)."""
    source, _, plan_id = parse_key(key)
    if source == "ozon" and any(k in changes for k in ("day", "amount", "skipped")):
        raise PlanningError("Выплаты Ozon приходят по графику Ozon — перенести их нельзя. График выплат — на странице "
                            "«Выплаты Ozon», а выключить их в прогнозе — переключателем «Выплаты Ozon»")
    if plan_id is not None:
        await current_plan(db, user_id, plan_id)
    if changes.get("day") is not None and planned_day is not None:
        if abs((changes["day"] - planned_day).days) > MAX_MOVE_DAYS:
            raise PlanningError(f"Перенести платёж можно не дальше чем на {MAX_MOVE_DAYS} дней от даты по плану")
    if changes.get("day") is not None and changes["day"] < planning_svc.MIN_DAY:
        raise PlanningError("Дата: слишком ранняя — проверьте год")
    if changes.get("amount") is not None:
        if changes["amount"] <= 0:
            raise PlanningError("Сумма — больше нуля")
        changes["amount"] = planning_svc._check_amount(changes["amount"])
    current = (await latest_overrides(db, user_id)).get(key)
    values = {k: getattr(current, k) if current else (False if k == "skipped" else None) for k in OVERRIDE_FIELDS}
    values.update(changes)
    if values["day"] is not None and planned_day is not None and values["day"] == planned_day:
        values["day"] = None
    row = PlannedOverride(user_id=user_id, key=key, **values)
    db.add(row)
    return row
