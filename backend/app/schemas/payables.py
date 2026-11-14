"""Схемы «Долгов поставщикам» (шаг 10.29)."""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

EntryKind = Literal["purchase", "payment"]


class SetupIn(BaseModel):
    start: date = Field(description="С какого дня считаем долг")
    amount: Decimal = Field(description="Долг на начало этого дня, ₽ (0 — долга не было)")
    due_day: date | None = Field(None, description="Срок оплаты начального долга; пусто — срок неизвестен")


class EntryIn(BaseModel):
    kind: EntryKind
    day: date
    amount: Decimal
    due_day: date | None = None
    number: str | None = None
    counterparty: str | None = None
    comment: str | None = None


class EntryPatch(BaseModel):
    day: date | None = None
    amount: Decimal | None = None
    due_day: date | None = None
    number: str | None = None
    counterparty: str | None = None
    comment: str | None = None


class EntryOut(BaseModel):
    entry_id: uuid.UUID
    kind: Literal["opening", "purchase", "payment"]
    day: date
    amount: Decimal
    due_day: date | None
    number: str | None
    counterparty: str | None
    comment: str | None
    source: Literal["manual", "import", "operation"]  # operation — из «Операций», правится там
    editable: bool
    version: int


class Schedule(BaseModel):
    period: Literal["week", "month"]
    day: int
    title: str


class Summary(BaseModel):
    debt: Decimal
    overdue: Decimal
    not_due: Decimal
    prepaid: Decimal
    accrued: Decimal
    paid: Decimal
    due_7: Decimal
    due_14: Decimal
    due_30: Decimal
    paid_30d: Decimal
    accrued_30d: Decimal
    overdue_count: int
    oldest_overdue_days: int


class Buckets(BaseModel):
    no_term: Decimal
    not_due: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_plus: Decimal


class OpenItem(BaseModel):
    kind: Literal["opening", "sale", "purchase"]
    label: str
    day: date
    due: date | None
    amount: Decimal
    remaining: Decimal
    overdue_days: int
    ref: str | None
    period_from: date | None
    period_to: date | None


class SeriesPoint(BaseModel):
    day: date
    debt: Decimal


class PayablesOut(BaseModel):
    enabled: bool
    supplier_payment: str
    mode: Literal["sales", "purchases"]
    configured: bool  # начало учёта задано
    today: date
    deferral_days: int | None
    start: date | None
    opening_amount: Decimal | None
    opening_due: date | None
    schedule: Schedule | None
    summary: Summary | None
    buckets: Buckets | None
    items: list[OpenItem]
    series: list[SeriesPoint]
    entries: list[EntryOut]
    warnings: list[str]
    incomplete: bool  # часть данных не учтена — долг «не менее»
    operations_counted: bool  # оплаты из «Операций» уже засчитаны


class ImportRowOut(BaseModel):
    line: int
    entry_id: uuid.UUID | None
    kind: str | None
    day: date | None
    amount: Decimal | None
    due_day: date | None
    number: str | None
    counterparty: str | None
    comment: str | None
    status: Literal["new", "changed", "unchanged", "skipped", "error"]
    message: str | None
