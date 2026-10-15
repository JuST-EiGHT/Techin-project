"""Банк (шаг 3.11): выписка файлом, счета с реквизитами, операции банка, дубли с ручными, сверка выплат Ozon."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, SecretStr

TxKind = Literal["regular", "ozon", "transfer"]


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class StatementAccount(BaseModel):
    number: str
    masked: str  # ···2345
    account: Ref | None  # найден по номеру или выбран; null — будет новый счёт «из банка»
    name: str  # как будет называться
    date_from: date | None
    date_to: date | None
    opening: Decimal | None  # остаток по выписке на начало
    closing: Decimal | None  # на конец


class StatementRow(BaseModel):
    day: date
    amount: Decimal
    account: str  # номер нашего счёта
    counterparty: str | None
    counterparty_inn: str | None
    purpose: str | None
    doc_number: str | None
    status: Literal["new", "duplicate"]
    kind: TxKind
    payer: str | None  # маркетплейс по ИНН плательщика
    ozon_payout: date | None  # подтверждает выплату Ozon этого дня — одна операция, а не две
    manual: list[uuid.UUID]  # похожие ручные операции — предложим объединить


class StatementCounts(BaseModel):
    new: int
    duplicate: int  # уже загружены раньше
    ozon: int  # поступления от Ozon
    ozon_linked: int  # из них подтверждают выплату по данным Ozon
    transfer: int  # переводы между своими счетами
    manual: int  # похожи на операции, внесённые вручную
    skipped: int  # документы не по счетам выписки или с ошибкой


class StatementPreview(BaseModel):
    applied: bool
    version: str | None
    encoding: str
    date_from: date | None
    date_to: date | None
    accounts: list[StatementAccount]
    counts: StatementCounts
    rows: list[StatementRow]
    skipped: list[str]
    warnings: list[str]
    already_loaded: datetime | None  # этот же файл загружали
    candidates: list[Ref]  # счета без номера — к ним можно привязать выписку
    suggested_account_id: uuid.UUID | None


class BankAccount(BaseModel):
    id: uuid.UUID
    name: str
    source: Literal["manual", "bank"]
    number: str | None
    bik: str | None
    bank_name: str | None
    archived: bool
    balance: Decimal | None  # по операциям сервиса
    bank_balance: Decimal | None  # по последней выписке
    bank_balance_day: date | None
    transactions: int


class BankTx(BaseModel):
    id: uuid.UUID
    account: Ref
    day: date
    amount: Decimal
    counterparty: str | None
    counterparty_inn: str | None
    counterparty_account: str | None
    purpose: str | None
    doc_number: str | None
    kind: TxKind
    source: str
    op_id: uuid.UUID | None


class BankTxPage(BaseModel):
    total: int
    items: list[BankTx]


class StatementInfo(BaseModel):
    id: uuid.UUID
    filename: str
    date_from: date | None
    date_to: date | None
    accounts: list[str]
    uploaded_at: datetime
    stats: dict


class OpBrief(BaseModel):
    op_id: uuid.UUID
    day: date
    amount: Decimal
    account: str
    counterparty: str | None
    category: str | None
    comment: str | None


class MergeSuggestion(BaseModel):
    bank: OpBrief
    manual: OpBrief


class MergeIn(BaseModel):
    bank_op_id: uuid.UUID
    manual_op_id: uuid.UUID


class OzonCheckRow(BaseModel):
    payout_day: date | None  # выплата по данным Ozon
    ozon_amount: Decimal | None
    bank_day: date | None  # поступление на счёт
    bank_amount: Decimal | None
    diff: Decimal | None
    status: Literal["ok", "diff", "no_bank", "waiting", "bank_only"]


class OzonCheck(BaseModel):
    has_bank: bool
    rows: list[OzonCheckRow]
    counts: dict[str, int]


class PayoutHint(BaseModel):
    """Подсказка «сколько реально пришло» по выписке — сохраняется только по подтверждению пользователя."""

    amount: Decimal
    count: int
    date_from: date
    date_to: date
    lag_weeks: int
    note: str


# ---------- банки по API (шаг 3.12) ----------

ConnectionStatus = Literal["active", "error", "auth"]  # работает | сбой при обновлении | нужен новый токен


class ConnectIn(BaseModel):
    provider: Literal["tbank", "tochka"]
    # SecretStr: токен не попадёт в repr / логи модели и в ответ об ошибке проверки
    token: SecretStr = Field(..., min_length=1, max_length=8000)


class ConnectionAccount(BaseModel):
    number: str  # маскированный: ···2345 — полный номер не нужен
    name: str | None


class BankConnectionOut(BaseModel):
    """Подключение банка. Токена здесь нет и не будет (правило 7)."""

    id: uuid.UUID
    provider: str
    title: str
    status: ConnectionStatus
    status_message: str | None
    expires_at: datetime | None
    accounts: list[ConnectionAccount]
    last_sync_at: datetime | None
    created_at: datetime


class ConnectResult(BaseModel):
    connection: BankConnectionOut
    replaced: bool  # тот же банк и счета — токен прежнего подключения заменён
    sync_queued: bool  # загрузка истории поставлена в очередь


class VerifyResult(BaseModel):
    ok: bool
    message: str
    connection: BankConnectionOut
