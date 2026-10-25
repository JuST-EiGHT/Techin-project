"""Выплаты Ozon (шаг 3.6) и расчёты с Ozon за период (10.23): ответы /api/v1/ozon/*."""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["auto", "standard", "daily", "weekly", "custom"]


class ScheduleOut(BaseModel):
    mode: Literal["daily", "weekly", "irregular"]
    weekday: int | None  # 0 — понедельник
    lag_weeks: int | None  # выплата за неделю продаж N недель назад
    fee_pct: Decimal  # комиссия Ozon за гибкий график, %
    source: Literal["auto", "standard", "user"]  # по выплатам / стандартный график Ozon / свой
    confidence: Literal["high", "medium", "low"] | None = None
    payouts_used: int = 0
    title: str


class Detected(BaseModel):
    mode: Literal["daily", "weekly", "irregular"]
    weekday: int | None
    lag_weeks: int | None
    fee_pct: Decimal
    confidence: Literal["high", "medium", "low"] | None
    title: str


class ForecastOut(BaseModel):
    date: date
    amount: Decimal | None  # null — оценить нечем
    kind: Literal["schedule", "estimate"]  # по начислениям закончившейся недели / оценка по среднему
    week_from: date | None  # неделя продаж, за которую выплата
    week_to: date | None


class PayoutFact(BaseModel):
    date: date
    amount: Decimal
    week_from: date | None
    week_to: date | None
    expected: Decimal | None  # по графику: начислено за неделю продаж − комиссия
    diff: Decimal | None


class WeekBar(BaseModel):
    week_from: date  # понедельник недели выплаты
    paid: Decimal
    schedule: Decimal
    estimate: Decimal


class PayoutsOut(BaseModel):
    has_data: bool
    last_day: date | None  # последний загруженный день баланса
    balance: Decimal | None  # на балансе у Ozon на конец последнего загруженного дня
    paid_30d: Decimal | None
    schedule: ScheduleOut
    detected: Detected | None  # что видно по выплатам (для настройки)
    next_payout: ForecastOut | None
    forecast: list[ForecastOut]
    history: list[PayoutFact]  # новые сверху, за 12 недель
    overdue: list[date]
    weeks: list[WeekBar]
    warnings: list[str]


class BalanceDay(BaseModel):
    date: date
    loaded: bool  # false — день не загружен: значения null, а не 0
    opening: Decimal | None
    accrued: Decimal | None
    payments: Decimal | None
    closing: Decimal | None
    sales: Decimal | None
    returns: Decimal | None
    services: Decimal | None


class PayoutSettingsIn(BaseModel):
    mode: Mode
    weekday: int | None = Field(None, ge=0, le=6)
    lag_weeks: int | None = Field(None, ge=0, le=8)
    fee_pct: Decimal | None = Field(None, ge=0, le=10)


class PayoutSettingsRow(BaseModel):
    effective_from: date
    mode: Mode
    weekday: int | None
    lag_weeks: int | None
    fee_pct: Decimal | None


class PayoutSettingsState(BaseModel):
    current: PayoutSettingsRow | None  # null — «определять автоматически» (ничего не меняли)
    history: list[PayoutSettingsRow]  # новые сверху
    standard: ScheduleOut  # стандартный график Ozon на сегодня
    created: bool | None = None


# ---------- «Выплаты и расчёты с Ozon» за период (шаг 10.23) ----------


class SettlementBalance(BaseModel):
    """Баланс у Ozon за период: на начало, начислено, выплачено, на конец; дни без данных — пропуск."""

    opening: Decimal | None
    opening_day: date
    accrued: Decimal
    paid: Decimal
    closing: Decimal | None
    closing_day: date
    days_total: int
    missing_days: int


class SettlementType(BaseModel):
    name: str
    amount: Decimal


class SettlementLine(BaseModel):
    key: str  # revenue, returns, adjustments, commission, logistics, storage, ads, other — строки P&L
    title: str
    amount: Decimal
    types: list[SettlementType]


class SettlementAccruals(BaseModel):
    rows: list[SettlementLine]
    total: Decimal  # начислено к выплате за период
    data_source: str
    missing_days: int


class SettlementPayout(BaseModel):
    """Выплата Ozon и её поступление на счёт. status: ok — пришло, diff — другой суммой, no_bank — не пришло (выписка за
    эти дни есть), waiting — выписки за эти дни нет (ещё не загружена или раньше первой), bank_only — поступление без
    выплаты в данных Ozon, no_statements — выписок нет вовсе."""

    status: Literal["ok", "diff", "no_bank", "waiting", "bank_only", "no_statements"]
    linked: bool  # пару связала загрузка выписки; иначе — найдена сверкой
    payout_day: date | None
    ozon_amount: Decimal | None
    expected: Decimal | None  # по графику
    week_from: date | None  # неделя продаж по графику
    week_to: date | None
    schedule_diff: Decimal | None  # выплачено − ожидали по графику
    bank_day: date | None
    bank_amount: Decimal | None
    diff: Decimal | None  # пришло на счёт − выплатил Ozon
    account_id: uuid.UUID | None
    op_id: uuid.UUID | None


class SettlementIssue(BaseModel):
    kind: Literal["bank_missing", "bank_diff", "bank_only", "schedule_diff", "overdue", "ozon_report"]
    severity: Literal["red", "yellow"]
    day: date
    title: str
    text: str
    amount: Decimal | None  # деньги под вопросом
    payout_day: date | None
    bank_day: date | None
    week_from: date | None
    account_id: uuid.UUID | None
    op_id: uuid.UUID | None


class SettlementTotals(BaseModel):
    paid: Decimal | None  # выплатил Ozon за период; null — выплат нет
    received: Decimal | None  # пришло на счёт по этим выплатам и без пары; null — выписок нет
    count: int


class SettlementSchedule(BaseModel):
    title: str
    reliable: bool  # с графиком сверяем выплаты (как аномалии 7.1)


class SettlementDocument(BaseModel):
    id: uuid.UUID
    period_start: date
    period_end: date
    file_name: str | None


class SettlementOut(BaseModel):
    date_from: date
    date_to: date
    has_balance: bool
    balance: SettlementBalance | None
    accruals: SettlementAccruals | None
    accrued_diff: Decimal | None  # начислено по балансу − по начислениям; null — не сравнить
    schedule: SettlementSchedule
    payouts: list[SettlementPayout]
    totals: SettlementTotals
    has_bank: bool
    bank_until: date | None
    counts: dict[str, int]
    issues: list[SettlementIssue]
    documents: list[SettlementDocument]
