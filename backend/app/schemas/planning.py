"""Планирование (шаг 3.8): ответы и запросы /api/v1/planning/*."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.settings import SupplierPayment

AccountKind = Literal["checking", "card", "cash", "wallet", "other"]
CategoryKind = Literal["income", "expense", "transfer"]
Source = Literal["manual", "import", "ozon_payout", "bank"]


class PlanningState(BaseModel):
    enabled: bool
    setup_done: bool  # мастер пройден; выключенный раздел включается без мастера, данные остаются
    ozon_account_id: uuid.UUID | None  # куда приходят выплаты Ozon
    threshold: Decimal | None
    horizon_days: int
    supplier_payment: SupplierPayment  # «Откуда товар» — та же настройка, что в «Основных настройках»
    supplier_deferral_days: int | None
    cost_label: str
    gen_ozon: bool  # генераторы плана (шаг 3.9): выплаты Ozon, оплата поставщику после продажи, налоги
    gen_supplier: bool
    gen_taxes: bool
    supplier_period: Literal["week", "month"]  # оплата поставщику после продажи: раз в неделю или месяц
    supplier_day: int  # день недели 1–7 или число месяца 1–31


class PlanningSettingsIn(BaseModel):
    """Не переданное поле не меняется; ozon_account_id: null — выплаты Ozon никуда не записываются."""

    enabled: bool | None = None
    ozon_account_id: uuid.UUID | None = None
    threshold: Decimal | None = Field(None, ge=-10_000_000_000, le=10_000_000_000)
    horizon_days: int | None = Field(None, ge=14, le=180)
    gen_ozon: bool | None = None
    gen_supplier: bool | None = None
    gen_taxes: bool | None = None
    supplier_period: Literal["week", "month"] | None = None
    supplier_day: int | None = Field(None, ge=1, le=31)


class TemplateCategory(BaseModel):
    code: str
    name: str
    kind: CategoryKind
    group: Literal["common", "resale", "production", "simple"]  # simple — «Приход» / «Расход» быстрого старта
    why: str  # зачем статья — рядом с галочкой
    required: bool = False  # нужна сервису — снять нельзя


class SetupOptions(BaseModel):
    categories: list[TemplateCategory]
    groups_by_supplier: dict[str, list[str]]  # какие группы предложить по ответу «Откуда товар» ("" — пропущен)


class SetupAccountIn(BaseModel):
    name: str = Field(..., max_length=100)
    kind: AccountKind
    balance: Decimal | None = None  # пусто — остаток не указан


class SetupIn(BaseModel):
    quick: bool = False  # «Пропустить настройку»: один счёт (сюда же выплаты Ozon), статьи «Приход» и «Расход»
    supplier_payment: SupplierPayment | None = None  # null — вопрос пропущен
    supplier_deferral_days: int | None = Field(None, ge=1, le=365)
    accounts: list[SetupAccountIn] = Field(default_factory=list, max_length=20)
    ozon_account: int | None = Field(None, ge=0)  # номер счёта в accounts; null — вопрос пропущен
    categories: list[str] = Field(default_factory=list, max_length=100)


class Category(BaseModel):
    id: uuid.UUID
    name: str
    kind: CategoryKind
    code: str | None
    archived: bool
    required: bool
    why: str | None


class CategoryIn(BaseModel):
    name: str = Field(..., max_length=100)
    kind: Literal["income", "expense"]


class CategoryPatch(BaseModel):
    name: str | None = Field(None, max_length=100)
    archived: bool | None = None


class TemplateCodes(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=100)


class Opening(BaseModel):
    amount: Decimal
    as_of: date  # остаток на начало этого дня


class Account(BaseModel):
    id: uuid.UUID
    name: str
    kind: AccountKind
    source: Literal["manual", "bank"]
    archived: bool
    opening: Opening | None  # null — остаток не указан
    balance: Decimal | None  # на сегодня; null — неизвестен (нет начального остатка), а не 0
    receives_ozon: bool
    number: str | None = None  # номер счёта в банке (выписка, шаг 3.11)
    bank_name: str | None = None


class AccountIn(BaseModel):
    name: str = Field(..., max_length=100)
    kind: AccountKind
    balance: Decimal | None = None
    as_of: date | None = None  # по умолчанию — сегодня


class AccountPatch(BaseModel):
    name: str | None = Field(None, max_length=100)
    kind: AccountKind | None = None
    balance: Decimal | None = None  # новый начальный остаток — новая запись, прежний — в истории
    as_of: date | None = None
    archived: bool | None = None


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class CategoryRef(Ref):
    kind: CategoryKind


class TransferInfo(BaseModel):
    id: uuid.UUID
    direction: Literal["out", "in"]
    account: Ref | None  # второй счёт перевода


class Operation(BaseModel):
    op_id: uuid.UUID
    version: int
    day: date
    amount: Decimal  # + приход, − расход
    account: Ref
    category: CategoryRef | None
    counterparty: str | None
    comment: str | None
    source: Source
    transfer: TransferInfo | None
    before_opening: bool  # раньше начального остатка счёта — в остаток не входит


class Totals(BaseModel):
    income: Decimal
    expense: Decimal  # отрицательное число
    net: Decimal


class OperationsPage(BaseModel):
    date_from: date
    date_to: date
    total: int
    limit: int
    offset: int
    items: list[Operation]
    totals: Totals  # без переводов между своими счетами


class OperationIn(BaseModel):
    type: Literal["income", "expense", "transfer"]
    amount: Decimal = Field(..., gt=0)  # знак — по типу
    day: date
    account_id: uuid.UUID  # у перевода — со счёта
    to_account_id: uuid.UUID | None = None  # у перевода — на счёт
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(None, max_length=100)  # вписанная статья: есть — она, нет — новая в списке
    counterparty: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=2000)  # описание


class OperationPatch(BaseModel):
    """Не переданное поле не меняется. Сумма — положительная, знак — по type (не передан — как был)."""

    type: Literal["income", "expense"] | None = None
    amount: Decimal | None = Field(None, gt=0)
    day: date | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(None, max_length=100)
    counterparty: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=2000)  # описание


class OperationSaved(BaseModel):
    op_id: uuid.UUID
    version: int
    changed: bool
    category: Ref | None = None  # статья операции, если указывали
    category_note: Literal["existing", "created", "restored"] | None = None  # вписанная статья: была, новая, из архива


class OperationVersion(BaseModel):
    version: int
    day: date
    amount: Decimal
    account: str
    category: str | None
    counterparty: str | None
    comment: str | None
    source: Source
    created_at: datetime
    superseded_at: datetime | None
    deleted_at: datetime | None


class ImportRowOut(BaseModel):
    line: int
    op_id: uuid.UUID | None
    day: date | None
    amount: Decimal | None
    account: str | None
    category: str | None
    counterparty: str | None
    comment: str | None
    status: Literal["new", "changed", "unchanged", "skipped", "error"]
    message: str | None


class ImportCounts(BaseModel):
    new: int
    changed: int
    unchanged: int
    skipped: int  # пустая строка или такая операция уже есть
    error: int


class ImportResponse(BaseModel):
    applied: bool  # False — только предпросмотр
    counts: ImportCounts
    rows: list[ImportRowOut]

