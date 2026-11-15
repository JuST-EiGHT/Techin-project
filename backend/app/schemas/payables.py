"""Схемы «Долгов поставщикам» (шаг 10.23a): поставки, продажи по ним, оплаты, загрузка документа."""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

LotStatus = Literal["selling", "awaiting", "done", "closed"]


class Summary(BaseModel):
    to_pay: Decimal  # продано и не оплачено (по известным ценам)
    accrued: Decimal  # начислено поставщику за проданное
    paid_total: Decimal
    paid_30d: Decimal
    prepaid: Decimal  # оплачено больше, чем начислено
    stock_qty: int  # ещё не продано в открытых поставках
    stock_value: Decimal  # ... по ценам поставок — долг, который возникнет по мере продаж
    stock_unknown_qty: int  # из них без цены
    unknown_price_qty: int  # продано без себестоимости — долг неизвестен
    lots_total: int
    lots_selling: int
    lots_awaiting: int
    lots_done: int
    lots_closed: int
    unallocated_qty: int


class LotLineOut(BaseModel):
    article: str
    name: str | None
    barcode: str | None
    offer: str | None  # артикул товара, с которым сопоставили
    in_catalog: bool
    qty: int
    sold_before: int
    sold: int  # по данным сервиса, за вычетом возвратов
    returned: int
    written_off: int
    remaining: int
    price: Decimal | None
    amount: Decimal | None  # (продано + уже продано ранее) × цена


class MoveOut(BaseModel):
    day: date
    article: str
    kind: Literal["sale", "return"]
    qty: int


class LotOut(BaseModel):
    lot_id: uuid.UUID
    version: int
    number: str
    day: date
    supplier: str | None
    receiver: str | None
    warehouse: str | None
    source: str
    doc_total_qty: int | None
    has_document: bool
    comment: str | None
    closed_on: date | None
    close_reason: str | None
    status: LotStatus
    qty: int
    sold: int
    remaining: int
    debt: Decimal
    paid: Decimal
    unpaid: Decimal
    unknown_price_qty: int
    stock_value: Decimal
    lines: list[LotLineOut]
    moves: list[MoveOut]


class PaymentOut(BaseModel):
    payment_id: uuid.UUID
    day: date
    amount: Decimal
    counterparty: str | None
    comment: str | None
    source: Literal["manual", "operation"]  # operation — из «Операций», правится там
    editable: bool
    version: int


class Unallocated(BaseModel):
    article: str
    qty: int


class PayablesOut(BaseModel):
    enabled: bool
    today: date
    configured: bool  # есть хоть одна поставка
    summary: Summary
    warnings: list[str]
    sales_known_from: date | None  # с какого дня сервис видит продажи
    operations_counted: bool
    lots: list[LotOut]
    payments: list[PaymentOut]
    unallocated: list[Unallocated]


class PreviewLine(BaseModel):
    line: int
    article: str
    name: str | None
    barcode: str | None
    qty: int | None
    sold_before: int
    offer: str | None
    in_catalog: bool
    price: Decimal | None
    amount: Decimal | None
    error: str | None


class PreviewDoc(BaseModel):
    number: str | None
    day: date | None
    supplier: str | None
    receiver: str | None
    warehouse: str | None
    total_qty: int | None
    lines_qty: int
    status: Literal["new", "changed", "unchanged", "error"]
    lot_id: uuid.UUID | None
    errors: list[str]
    warnings: list[str]
    lines: list[PreviewLine]
    amount: Decimal
    no_price: int
    not_in_catalog: int
    older_than_sales: bool


class UploadOut(BaseModel):
    applied: bool
    format: str
    counts: dict[str, int]
    docs: list[PreviewDoc]
    sales_known_from: date | None


class LotPatch(BaseModel):
    comment: str | None = None
    sold_before: dict[str, int] | None = None  # артикул → «уже продано ранее»


class LotClose(BaseModel):
    reason: Literal["returned", "written_off", "other"]
    comment: str | None = None


class PaymentIn(BaseModel):
    day: date
    amount: Decimal
    counterparty: str | None = None
    comment: str | None = None


class PaymentPatch(BaseModel):
    day: date | None = None
    amount: Decimal | None = None
    counterparty: str | None = None
    comment: str | None = None
