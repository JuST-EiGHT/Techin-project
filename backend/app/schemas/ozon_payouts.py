"""Выплаты Ozon (шаг 3.6): ответы /api/v1/ozon/*."""
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
