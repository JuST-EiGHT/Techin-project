"""Плановые платежи и платёжный календарь (шаг 3.9): запросы и ответы /api/v1/planning/plans, /planning/calendar."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

PlanKind = Literal["income", "expense"]
Period = Literal["once", "week", "month", "quarter", "year"]
LineSource = Literal["ozon", "manual", "supplier", "tax"]
Status = Literal["planned", "overdue", "paid", "skipped", "disabled"]


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class PlanIn(BaseModel):
    kind: PlanKind
    amount: Decimal = Field(..., gt=0)
    start_date: date  # разовый — дата платежа; регулярный — с какой даты
    title: str | None = Field(None, max_length=200)  # пусто — по статье
    period: Period = "once"
    day: int | None = Field(None, ge=1, le=31)  # день недели 1–7 или число месяца; пусто — как у даты начала
    end_date: date | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(None, max_length=100)  # вписанная статья: есть — она, нет — новая в списке
    account_id: uuid.UUID | None = None
    counterparty: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=2000)
    enabled: bool = True
    purchase_day: date | None = None  # закупка с отсрочкой: оплата = закупка + N дней из «Откуда товар»


class PlanPatch(BaseModel):
    """Не переданное поле не меняется; изменение — новая версия плана."""

    kind: PlanKind | None = None
    amount: Decimal | None = Field(None, gt=0)
    start_date: date | None = None
    title: str | None = Field(None, max_length=200)
    period: Period | None = None
    day: int | None = Field(None, ge=1, le=31)
    end_date: date | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(None, max_length=100)
    account_id: uuid.UUID | None = None
    counterparty: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    purchase_day: date | None = None


class Plan(BaseModel):
    plan_id: uuid.UUID
    version: int
    kind: PlanKind
    title: str
    amount: Decimal
    period: Period
    day: int | None
    start_date: date
    end_date: date | None
    purchase_day: date | None
    enabled: bool
    category: Ref | None
    account: Ref | None
    counterparty: str | None
    comment: str | None
    source: Literal["manual", "import", "supplier"]
    schedule: str  # «раз в месяц, 5-го числа, с 01.11.2026»
    next_day: date | None  # ближайший платёж с сегодня; null — больше не будет


class PlanSaved(BaseModel):
    plan_id: uuid.UUID
    version: int
    changed: bool
    category_note: Literal["existing", "created", "restored"] | None = None


class PlanVersion(BaseModel):
    version: int
    kind: PlanKind
    title: str
    amount: Decimal
    schedule: str
    enabled: bool
    category: str | None
    account: str | None
    counterparty: str | None
    comment: str | None
    created_at: datetime
    superseded_at: datetime | None
    deleted_at: datetime | None


class OverrideIn(BaseModel):
    """Отдельный платёж: перенести (day), своя сумма (amount), пропустить (skipped) или вернуть как по плану (reset).
    Не переданное поле не меняется; day: null — дата по плану, amount: null — сумма по плану."""

    key: str = Field(..., max_length=120)
    day: date | None = None
    amount: Decimal | None = Field(None, gt=0)
    skipped: bool | None = None
    reset: bool = False


class PaidIn(BaseModel):
    """«Оплачено»: op_id — уже внесённая операция; иначе создаём операцию (дата, сумма, счёт — по умолчанию из плана)."""

    key: str = Field(..., max_length=120)
    op_id: uuid.UUID | None = None
    day: date | None = None
    amount: Decimal | None = Field(None, gt=0)
    account_id: uuid.UUID | None = None


class KeyIn(BaseModel):
    key: str = Field(..., max_length=120)


class PaidOut(BaseModel):
    op_id: uuid.UUID | None  # null — отметка без операции (планирование не включено)
    created: bool  # создана новая операция


class Range(BaseModel):
    date_from: date
    date_to: date


class Fact(BaseModel):
    op_id: uuid.UUID | None  # null — отметка «оплачено» без операции
    day: date
    amount: Decimal
    marked: bool  # отмечено «оплачено» (иначе найдено по сумме и дате)


class Notice(BaseModel):
    day: date
    doc: str | None  # «Уведомление», «Декларация»…


class Line(BaseModel):
    key: str  # ключ платежа — для переноса и «оплачено»
    source: LineSource
    title: str
    kind: PlanKind
    day: date  # после переноса
    planned_day: date
    amount: Decimal | None  # + поступление, − платёж; null — сумма пока неизвестна
    plan_amount: Decimal | None
    status: Status
    estimate: bool  # сумма — оценка
    enabled: bool
    moved: bool
    own_amount: bool
    skipped: bool
    fact: Fact | None
    diff: Decimal | None  # факт − план, если разошлись
    plan_id: uuid.UUID | None
    category: Ref | None
    account: Ref | None
    counterparty: str | None
    comment: str | None
    schedule: str | None
    note: str | None  # как посчитана сумма
    link: str | None
    period: Range | None  # за какой период (налоги, поставщик)
    period_label: str | None
    notice: Notice | None  # срок уведомления или декларации
    calc: Range | None  # расчёт суммы — P&L за этот период
    editable: bool  # выплату Ozon не переносят


class FactLine(BaseModel):
    """Операция без плана («вне плана»)."""

    op_id: uuid.UUID
    day: date
    amount: Decimal
    title: str
    category: str | None
    source: str


class ForecastDay(BaseModel):
    day: date
    income: Decimal
    expense: Decimal
    balance: Decimal | None  # null — остаток сейчас неизвестен


class Gap(BaseModel):
    date_from: date
    date_to: date
    min_balance: Decimal
    min_day: date
    below_zero: bool  # ниже нуля; иначе — ниже порога


class Generator(BaseModel):
    enabled: bool
    available: bool  # поставщик — только «после продажи» или «по-разному»; налоги — если задан налоговый профиль
    period: Literal["week", "month"] | None = None
    day: int | None = None


class Generators(BaseModel):
    ozon: Generator
    supplier: Generator
    taxes: Generator


class Hint(BaseModel):
    code: str
    name: str
    why: str


class CalendarOut(BaseModel):
    date_from: date
    date_to: date
    today: date
    start_balance: Decimal | None  # остаток сейчас по счетам; null — не указан ни у одного
    unknown_accounts: list[str]
    threshold: Decimal | None
    horizon_days: int
    generators: Generators
    supplier_payment: str
    deferral_days: int | None  # закупка с отсрочкой: через сколько дней оплата
    hints: list[Hint]  # своё производство: регулярные расходы, которые стоит запланировать
    lines: list[Line]
    facts: list[FactLine]
    days: list[ForecastDay]  # прогноз остатка с сегодня
    gaps: list[Gap]
    first_below_zero: date | None  # денег хватит до дня перед этим; null — на весь период
    first_below_threshold: date | None
    min_balance: Decimal | None
    end_balance: Decimal | None
    overdue: int
    warnings: list[str]


class UpcomingTaxes(BaseModel):
    has_profile: bool
    planning: bool  # «Планирование» включено — есть календарь
    items: list[Line]


class PastChange(BaseModel):
    key: str
    title: str
    period_label: str | None
    day: date
    old: Decimal | None
    new: Decimal | None
    removed: bool
    added: bool


# ---------- обзор и ДДС (шаг 3.10) ----------


class AccountBalance(BaseModel):
    id: uuid.UUID
    name: str
    balance: Decimal | None  # null — остаток не указан


class NextOzon(BaseModel):
    day: date
    amount: Decimal | None
    estimate: bool
    title: str


class OzonState(BaseModel):
    enabled: bool  # выплаты Ozon включены в прогноз
    has_key: bool  # ключ Ozon API подключён — без него выплат в прогнозе нет


class Expected(BaseModel):
    """Ожидается с сегодня до конца горизонта: выплаты Ozon, прочие поступления, платежи (отрицательное)."""

    ozon: Decimal
    income: Decimal
    expense: Decimal
    unknown: int  # платежей, сумма которых пока неизвестна, — в суммы не вошли


class SeriesDay(BaseModel):
    day: date
    kind: Literal["fact", "plan"]  # до сегодня — факт, с сегодня — план
    balance: Decimal | None  # остаток на конец дня; null — неизвестен
    ozon: Decimal
    income: Decimal
    expense: Decimal
    fact: dict[str, Decimal] | None  # сегодня: что уже пришло и ушло (ozon, income, expense)


class Suggestion(BaseModel):
    code: str
    name: str
    why: str
    have: bool  # статья уже есть у пользователя


class EmptyState(BaseModel):
    has_ops: bool  # свои операции (кроме выплат Ozon)
    has_plans: bool
    suggestions: list[Suggestion]  # регулярные платежи по профилю — когда планов ещё нет


class OverviewOut(BaseModel):
    today: date
    date_from: date
    date_to: date
    horizon_days: int
    balance: Decimal | None
    unknown_accounts: list[str]
    accounts: list[AccountBalance]
    threshold: Decimal | None
    next_ozon: NextOzon | None
    ozon: OzonState
    first_below_zero: date | None
    first_below_threshold: date | None
    min_balance: Decimal | None
    end_balance: Decimal | None
    gaps: list[Gap]
    expected: Expected
    series: list[SeriesDay]
    upcoming: list[Line]  # на 14 дней и неотмеченные прошлые
    empty: EmptyState
    warnings: list[str]


class CashFlowPeriod(BaseModel):
    date_from: date
    date_to: date
    opening: Decimal | None
    income: Decimal
    expense: Decimal
    net: Decimal
    closing: Decimal | None


class CashFlowRow(BaseModel):
    category_id: uuid.UUID | None  # null — без статьи
    name: str
    section: Literal["income", "expense"]
    values: list[Decimal]  # по периодам
    total: Decimal


class CashFlowOut(BaseModel):
    date_from: date
    date_to: date
    granularity: Literal["week", "month"]
    opening: Decimal | None  # null — остаток на начало неизвестен
    closing: Decimal | None
    income: Decimal
    expense: Decimal
    net: Decimal
    periods: list[CashFlowPeriod]
    rows: list[CashFlowRow]
    has_data: bool
    warnings: list[str]
