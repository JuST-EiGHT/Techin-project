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
    parent_id: uuid.UUID | None = None  # подстатья этой статьи (10.19)
    has_children: bool = False
    in_pnl: bool = False  # прочий расход бизнеса в P&L (10.22)
    in_budget: bool = True  # статья бюджетов (10.21)
    operations: int = 0  # действующих операций со статьёй


class CategoryIn(BaseModel):
    name: str = Field(..., max_length=100)
    kind: Literal["income", "expense"]
    parent_id: uuid.UUID | None = None
    in_pnl: bool | None = None
    in_budget: bool | None = None


class CategoryPatch(BaseModel):
    """Не переданное поле не меняется; parent_id: null — статья верхнего уровня."""

    name: str | None = Field(None, max_length=100)
    archived: bool | None = None
    parent_id: uuid.UUID | None = None
    in_pnl: bool | None = None
    in_budget: bool | None = None


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
    parent_id: uuid.UUID | None = None  # подстатья — статья, в которую она входит


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
    # кто поставил статью (шаг 3.12): user — вручную, rule — правило, auto — встроенное правило; null — статьи нет
    category_by: Literal["user", "rule", "auto"] | None = None


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


class RuleSuggestion(BaseModel):
    """После ручной разметки: «создать правило для всех операций этого контрагента?»"""

    direction: Literal["in", "out"]
    counterparty_inn: str | None  # условие правила — ИНН (из банка) …
    counterparty: str | None  # … или название, если ИНН нет
    counterparty_name: str | None  # для текста предложения
    category: Ref
    others: int  # сколько операций без ручной разметки поменяет правило


class OperationSaved(BaseModel):
    op_id: uuid.UUID
    version: int
    changed: bool
    category: Ref | None = None  # статья операции, если указывали
    category_note: Literal["existing", "created", "restored"] | None = None  # вписанная статья: была, новая, из архива
    rule_suggestion: RuleSuggestion | None = None  # статью поставили вручную — можно сделать правилом (шаг 3.12)


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



# ---------- правила разнесения (шаг 3.12) ----------

RuleDirection = Literal["in", "out"]


class RuleIn(BaseModel):
    """Условия складываются: все указанные должны совпасть. Хотя бы одно, кроме направления, обязательно."""

    direction: RuleDirection  # in — поступления, out — платежи
    category_id: uuid.UUID
    counterparty_inn: str | None = Field(None, max_length=20)
    counterparty: str | None = Field(None, max_length=200)  # слова в названии контрагента
    purpose: str | None = Field(None, max_length=500)  # слова в назначении, через запятую — любое
    amount_min: Decimal | None = Field(None, ge=0, le=10_000_000_000)
    amount_max: Decimal | None = Field(None, ge=0, le=10_000_000_000)
    enabled: bool = True


class RulePatch(BaseModel):
    direction: RuleDirection | None = None
    category_id: uuid.UUID | None = None
    counterparty_inn: str | None = Field(None, max_length=20)
    counterparty: str | None = Field(None, max_length=200)
    purpose: str | None = Field(None, max_length=500)
    amount_min: Decimal | None = Field(None, ge=0, le=10_000_000_000)
    amount_max: Decimal | None = Field(None, ge=0, le=10_000_000_000)
    enabled: bool | None = None


class RuleOut(BaseModel):
    id: uuid.UUID
    priority: int
    direction: RuleDirection
    counterparty_inn: str | None
    counterparty: str | None
    purpose: str | None
    amount_min: Decimal | None
    amount_max: Decimal | None
    category: CategoryRef | None
    enabled: bool
    operations: int  # сколько операций сейчас разнесено этим правилом


class BuiltinRule(BaseModel):
    code: str
    title: str
    when: str
    category: str
    why: str


class RulesPage(BaseModel):
    builtin: list[BuiltinRule]  # встроенные — всегда раньше ваших
    rules: list[RuleOut]


class RuleSaved(BaseModel):
    rule: RuleOut | None
    applied: int  # у скольких операций без ручной разметки поменялась статья


class RuleExample(BaseModel):
    op_id: uuid.UUID
    day: date
    amount: Decimal
    counterparty: str | None
    comment: str | None


class RulePreview(BaseModel):
    matched: int  # операций без ручной разметки под эти условия
    examples: list[RuleExample]


class RuleOrder(BaseModel):
    ids: list[uuid.UUID] = Field(..., max_length=300)


class JournalPlan(BaseModel):
    key: str  # ключ платежа календаря — для «Оплачено» (POST /planning/calendar/paid)
    title: str
    plan_id: uuid.UUID | None  # свой плановый платёж — правится в «Календаре»
    estimate: bool  # сумма — оценка
    link: str | None


class JournalRow(BaseModel):
    """Строка журнала: факт — операция (как в /operations), план — платёж календаря без оплаты."""

    id: str  # op:<op_id> | plan:<ключ>
    status: Literal["fact", "planned", "overdue"]
    type: Literal["income", "expense", "transfer"]
    day: date
    amount: Decimal | None  # + приход, − расход; у плана сумма может быть ещё неизвестна
    category: CategoryRef | None
    parent: Ref | None  # статья, если category — подстатья
    account: Ref | None
    counterparty: str | None
    comment: str | None
    source: Literal["manual", "import", "ozon_payout", "bank", "plan", "supplier", "tax", "ozon_forecast"]
    operation: Operation | None  # у факта
    plan: JournalPlan | None  # у плана; у факта — платёж, который он закрыл


class JournalTotals(BaseModel):
    fact: Totals  # без переводов
    plan: Totals  # ещё не оплаченное (плановое и просроченное), суммы-оценки — как есть


class JournalPage(BaseModel):
    date_from: date
    date_to: date
    total: int
    limit: int
    offset: int
    items: list[JournalRow]
    totals: JournalTotals
    counts: dict[str, int]  # fact / planned / overdue — по фильтрам, кроме статуса
    plan_note: str | None  # почему плановых нет (планирование выключено, период длиннее 400 дней)


class BulkIn(BaseModel):
    """Массовое изменение: передаются только поля, которые меняем. category_id: null — убрать статью."""

    op_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    counterparty: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=2000)


class BulkOut(BaseModel):
    changed: int  # операций с новой версией
    unchanged: int  # уже были такими


class CategorizeIn(BaseModel):
    op_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    category_id: uuid.UUID | None  # null — убрать статью (тогда операцию могут разнести правила)


class CategorizeOut(BaseModel):
    changed: int
