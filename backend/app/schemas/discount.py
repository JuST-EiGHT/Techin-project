"""Заявки покупателей на скидку (шаг 4.3a)."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DiscountProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None = None
    sku: int | None = None
    barcode: str | None = None


class DiscountLastDecision(BaseModel):
    action: str  # approve | decline
    status: str  # applied | blocked | rejected | failed | unknown
    at: datetime
    price: Decimal | None = None
    quantity: int | None = None
    error: str | None = None


class DiscountRow(BaseModel):
    id: uuid.UUID
    ozon_task_id: int
    sku: int | None = None
    name: str | None = None  # название из заявки Ozon
    product: DiscountProduct | None = None  # товар каталога; нет — заявка на товар, которого нет в каталоге
    status: str  # статус Ozon: NEW | APPROVED | DECLINED
    state: str  # для вкладок: NEW | APPROVED | DECLINED | EXPIRED
    state_title: str
    can_decide: bool
    created_at: datetime | None = None
    end_at: datetime | None = None
    edited_till: datetime | None = None
    hours_left: Decimal | None = None
    requested_price: Decimal | None = None
    requested_discount_pct: Decimal | None = None  # как считает Ozon
    discount_pct: Decimal | None = None  # от вашей цены сейчас
    requested_quantity: int | None = None
    price: Decimal | None = None  # ваша цена в Ozon сейчас
    original_price: Decimal | None = None
    min_auto_price: Decimal | None = None  # минимальная цена в Ozon (после автоприменения скидок)
    breakeven_price: Decimal | None = None  # минимальная для участия (калькулятор 4.2)
    target_price: Decimal | None = None
    margin: Decimal | None = None  # при запрошенной цене, %
    unit_profit: Decimal | None = None  # с 1 шт. при запрошенной цене, ₽
    total_profit: Decimal | None = None  # × сколько просят
    stock: int | None = None
    marks: list[str] = []
    mark_titles: list[str] = []
    rule_ok: bool | None = None  # «одобрили бы по правилу»: None — правило не задано или расчёта нет
    missing: list[str] = []  # чего не хватает для расчёта
    approved_price: Decimal | None = None
    approved_quantity: int | None = None
    approved_margin: Decimal | None = None
    moderated_at: datetime | None = None
    is_auto_moderated: bool | None = None
    first_seen_at: datetime | None = None
    decision: DiscountLastDecision | None = None  # последнее решение через сервис


class DiscountCounts(BaseModel):
    new: int = 0
    approved: int = 0
    declined: int = 0
    expired: int = 0
    all: int = 0
    loss: int = 0  # новые «в минус»


class DiscountList(BaseModel):
    items: list[DiscountRow]
    counts: DiscountCounts
    low_margin_pct: Decimal  # порог «низкой маржи» пользователя
    rule_margin_pct: Decimal | None = None  # «одобрили бы по правилу»: маржа ≥ X %
    expiry_hours: int  # «скоро истечёт» — меньше стольких часов


class DiscountEvent(BaseModel):
    at: datetime
    status: str
    title: str
    approved_price: Decimal | None = None
    approved_quantity: int | None = None
    source: str  # ozon | service
    is_auto_moderated: bool | None = None


class DiscountDecisionOut(BaseModel):
    at: datetime
    action: str
    status: str
    status_title: str
    approved_price: Decimal | None = None
    approved_quantity: int | None = None
    comment: str | None = None
    error: str | None = None
    errors: list[str] = []
    margin: Decimal | None = None


class DiscountDetail(DiscountRow):
    events: list[DiscountEvent] = []
    decisions: list[DiscountDecisionOut] = []


class DecisionItemIn(BaseModel):
    task_id: uuid.UUID
    action: Literal["approve", "decline"]
    price: Decimal | None = Field(None, gt=0, le=100_000_000, decimal_places=2)  # своя цена; нет — запрошенная
    quantity: int | None = Field(None, ge=1, le=100_000)  # сколько штук; нет — сколько просят


class DecisionPreviewIn(BaseModel):
    items: list[DecisionItemIn] = Field(..., min_length=1, max_length=500)
    comment: str | None = Field(None, max_length=500)  # комментарий продавца — уходит в Ozon


class DecisionPreviewItem(BaseModel):
    preview_id: uuid.UUID
    task_id: uuid.UUID
    ozon_task_id: int
    action: str
    product: dict | None = None
    name: str | None = None
    requested_price: Decimal | None = None
    requested_quantity: int | None = None
    price: Decimal | None = None
    approved_price: Decimal | None = None
    approved_quantity: int | None = None
    discount_pct: Decimal | None = None
    breakeven_price: Decimal | None = None
    margin: Decimal | None = None
    unit_profit: Decimal | None = None
    total_profit: Decimal | None = None
    errors: list[str]
    warnings: list[str]
    needs_loss_ack: bool
    can_apply: bool


class DecisionSummary(BaseModel):
    total: int
    ok: int
    blocked: int
    approve: int
    decline: int
    loss: int  # одобрений в минус — нужно «понимаю»
    needs_confirm: bool  # больше 10 — подтверждение


class DecisionPreview(BaseModel):
    batch_id: uuid.UUID
    expires_at: datetime
    comment: str | None = None
    items: list[DecisionPreviewItem]
    summary: DecisionSummary


class DecisionApplyIn(BaseModel):
    batch_id: uuid.UUID
    confirmed: bool = False  # больше 10 заявок — «проверил список»
    accept_loss: bool = False  # «понимаю: продажа в минус»


class DecisionResultItem(BaseModel):
    task_id: uuid.UUID
    ozon_task_id: int
    action: str
    status: str
    status_title: str
    approved_price: Decimal | None = None
    approved_quantity: int | None = None
    error: str | None = None


class DecisionResult(BaseModel):
    batch_id: uuid.UUID
    items: list[DecisionResultItem]
    applied: int
    failed: int
    message: str


class RuleIn(BaseModel):
    margin_pct: Decimal | None = Field(None, ge=-90, le=90, decimal_places=2)  # null — правило выключено


class RuleOut(BaseModel):
    margin_pct: Decimal | None = None
