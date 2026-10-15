"""Банк — необязательный источник факта для «Планирования» (шаги 3.11–3.12).

Выписка файлом (формат обмена с 1С) или API банка пишут в одни таблицы. Правило 12: операции банка не удаляются и не
перезаписываются; повторная загрузка того же периода не создаёт дублей — ключ `dedup_key` (id операции банка; для файла —
номер документа + дата + сумма + счёт контрагента) уникален в пределах счёта. Операция банка становится операцией
планирования (`money_operations`, источник bank) — `op_id`; поступление от Ozon — та же операция, что выплата Ozon.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, LargeBinary, Numeric, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TX_KINDS = ("regular", "ozon", "transfer")  # обычная, поступление от Ozon, перевод между своими счетами


def _now() -> datetime:
    return datetime.now(timezone.utc)


CONNECTION_STATUSES = ("active", "error", "auth")  # работает | сбой при обновлении | токен не принят — нужен новый


class BankConnection(Base):
    """Банк по API (шаг 3.12). Токен — только Fernet-шифротекст: не логируется и не возвращается (правило 7).
    Права — только чтение счетов, остатков и выписок. Отключение удаляет токен; загруженные операции остаются.
    `state` — по номеру счёта: до какого дня загружено (`synced_to`); нет записи — при обновлении грузим историю."""

    __tablename__ = "bank_connections"
    __table_args__ = (Index("ix_bank_connections_user", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # tbank | tochka
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    status_message: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # срок токена, если известен
    accounts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # [{number, name}] — что видит токен
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # последнее успешное обновление
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())

    def __repr__(self) -> str:  # без токена — на случай попадания объекта в лог
        return f"<BankConnection {self.id} {self.provider}>"


class BankStatement(Base):
    """Загруженный файл выписки — исходные данные (правило 12): тот же файл второй раз не сохраняется (`sha256`)."""

    __tablename__ = "bank_statements"
    __table_args__ = (Index("ix_bank_statements_user_sha", "user_id", "sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)  # 1c
    version: Mapped[str | None] = mapped_column(String(16))  # 1.02 | 1.03
    encoding: Mapped[str] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    accounts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # номера счетов выписки
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # что сделали при загрузке
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "account_id", "dedup_key", name="uq_bank_transactions_key"),
        Index("ix_bank_transactions_user_day", "user_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("bank_statements.id", ondelete="SET NULL"))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # + приход, − расход
    counterparty: Mapped[str | None] = mapped_column(String(300))
    counterparty_inn: Mapped[str | None] = mapped_column(String(12))
    counterparty_account: Mapped[str | None] = mapped_column(String(20))
    counterparty_bik: Mapped[str | None] = mapped_column(String(9))
    purpose: Mapped[str | None] = mapped_column(String(2000))  # назначение платежа
    doc_number: Mapped[str | None] = mapped_column(String(64))
    bank_id: Mapped[str | None] = mapped_column(String(128))  # id операции в банке (API, шаг 3.12)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # file_1c | api_<банк>
    connection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("bank_connections.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="regular")  # regular | ozon | transfer
    op_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # операция планирования (money_operations.op_id)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class BankBalance(Base):
    """Остаток по данным банка на начало (`opening`) или конец (`closing`) дня. Такой же — без новой записи."""

    __tablename__ = "bank_balances"
    __table_args__ = (Index("ix_bank_balances_account_day", "account_id", "day"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("bank_statements.id", ondelete="SET NULL"))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # opening | closing
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class MergeDismissal(Base):
    """«Это разные операции»: операция банка и похожая ручная — не предлагать объединить снова."""

    __tablename__ = "operation_merge_dismissals"
    __table_args__ = (Index("ix_merge_dismissals_user", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    bank_op_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    manual_op_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())

