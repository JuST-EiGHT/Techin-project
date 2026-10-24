"""Планирование денег (шаг 3.8) — необязательный раздел, работает без банка.

Правило 12: исходные данные не перезаписываются.
- Начальный остаток счёта — записи `money_account_balances`: исправление — новая запись, действует последняя.
- Операция — версии одной логической операции (`op_id`): исправление — новая строка (`version` + 1), прежняя
  помечается `superseded_at`; удаление — `deleted_at` у действующей строки. Действует строка без обеих отметок.
- Счета и статьи — справочники: название меняется на месте, удаление — архив (операции ссылаются на них).
Перевод между своими счетами — две операции с общим `transfer_id` (− со счёта, + на счёт), не доход и не расход.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ACCOUNT_KINDS = ("checking", "card", "cash", "wallet", "other")  # расчётный счёт, карта, наличные, кошелёк, другое
CATEGORY_KINDS = ("income", "expense", "transfer")
OP_SOURCES = ("manual", "import", "ozon_payout", "bank")
DEFAULT_HORIZON_DAYS = 60
COMMENT_MAX = 2000  # описание операции


def _now() -> datetime:
    return datetime.now(timezone.utc)  # с микросекундами: порядок записей (последний остаток, версии) однозначен


class MoneyAccount(Base):
    __tablename__ = "money_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")  # manual | bank (3.11–3.12)
    # реквизиты счёта в банке (шаг 3.11): по номеру выписка находит свой счёт
    number: Mapped[str | None] = mapped_column(String(20))
    bik: Mapped[str | None] = mapped_column(String(9))
    bank_name: Mapped[str | None] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class MoneyAccountBalance(Base):
    """Начальный остаток счёта на начало дня `as_of`: операции с этого дня добавляются к нему."""

    __tablename__ = "money_account_balances"
    __table_args__ = (Index("ix_money_account_balances_account", "account_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class CashCategory(Base):
    """Статья движения денег. `code` — у статей из шаблона (по профилю бизнеса), у своих — None.

    Подстатьи (шаг 10.19): `parent_id` — статья того же вида, один уровень вложенности; факт подстатьи входит в статью.
    Где учитывается: ДДС — всегда (все деньги); `in_pnl` — в P&L как прочий расход бизнеса (шаг 10.22), `in_budget` — в
    бюджетах (шаг 10.21)."""

    __tablename__ = "cash_categories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # income | expense | transfer
    code: Mapped[str | None] = mapped_column(String(32))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("cash_categories.id", ondelete="SET NULL"))
    in_pnl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    in_budget: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class MoneyOperation(Base):
    """Факт движения денег: + приход, − расход. Версии — по `op_id` (см. описание модуля)."""

    __tablename__ = "money_operations"
    __table_args__ = (
        Index("ix_money_operations_user_day", "user_id", "day"),
        Index("ix_money_operations_op", "user_id", "op_id"),
        Index("ix_money_operations_external", "user_id", "external_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    op_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("cash_categories.id", ondelete="SET NULL"))
    counterparty: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX))  # описание: в списке — первые слова, полностью — в карточке
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|import|ozon_payout|bank
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # перевод: две операции с одним transfer_id
    external_key: Mapped[str | None] = mapped_column(String(64))  # выплата Ozon: ozon_payout:<дата> — без дублей
    # кто поставил статью (шаг 3.12): user — вручную (правила её не трогают), rule — правило `rule_id`,
    # auto — встроенное правило (маркетплейс по ИНН, перевод между своими счетами, налог); None — статьи нет или старые данные
    category_by: Mapped[str | None] = mapped_column(String(8))
    rule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # исправлена новой версией
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # скрыта пользователем


class PlanningSettings(Base):
    """Настройки раздела. Строки нет — раздел не включался."""

    __tablename__ = "planning_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    setup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # мастер пройден
    ozon_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("money_accounts.id", ondelete="SET NULL")
    )  # куда приходят выплаты Ozon
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # порог остатка: ниже — кассовый разрыв
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_HORIZON_DAYS)
    # генераторы плана (шаг 3.9) — каждый включается отдельно
    gen_ozon: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    gen_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    gen_taxes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    supplier_period: Mapped[str] = mapped_column(String(8), nullable=False, default="week", server_default="week")
    supplier_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")  # день недели / число
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


RULE_DIRECTIONS = ("in", "out")  # поступление | платёж


class CategorizationRule(Base):
    """Правило разнесения (шаг 3.12): условия → статья. Условия складываются (все указанные должны совпасть):
    ИНН контрагента, слова в названии контрагента, слова в назначении (через запятую — любое), направление, сумма от / до.
    Порядок — `priority` (меньше — раньше), срабатывает первое подошедшее. Ручная разметка важнее правил. Правило —
    настройка, а не исходные данные: меняется на месте, удаление — `deleted_at` (операции помнят `rule_id`)."""

    __tablename__ = "categorization_rules"
    __table_args__ = (Index("ix_categorization_rules_user", "user_id", "priority"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # in | out
    counterparty_inn: Mapped[str | None] = mapped_column(String(12))
    counterparty: Mapped[str | None] = mapped_column(String(200))  # слова в названии контрагента
    purpose: Mapped[str | None] = mapped_column(String(500))  # слова в назначении платежа, через запятую — любое
    amount_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    amount_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cash_categories.id", ondelete="CASCADE"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


PLAN_KINDS = ("income", "expense")
PLAN_PERIODS = ("once", "week", "month", "quarter", "year")
PLAN_SOURCES = ("manual", "import", "supplier")  # supplier — закупка с отсрочкой: дата оплаты = дата закупки + N дней


class PlannedPayment(Base):
    """Плановый платёж или поступление (шаг 3.9): разовый или регулярный. Версии — по `plan_id`, как у операций:
    изменение — новая строка (`version` + 1), прежняя помечается `superseded_at`; удаление — `deleted_at`.

    Регулярный: с `start_date` (и до `end_date`, если указан) раз в неделю / месяц / квартал / год; `day` — день недели
    1–7 (неделя) или число месяца 1–31 (31 — последний день месяца). Квартал и год отсчитываются от месяца начала.
    `purchase_day` — закупка с отсрочкой (профиль «с отсрочкой N дней»): дата оплаты = закупка + N дней из настроек,
    поэтому при смене N платёж сдвигается сам. Выключенный план (`enabled`) в прогнозе не участвует («что если»).
    """

    __tablename__ = "planned_payments"
    __table_args__ = (Index("ix_planned_payments_plan", "user_id", "plan_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # income | expense
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # > 0; знак — по kind
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("cash_categories.id", ondelete="SET NULL"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("money_accounts.id", ondelete="SET NULL"))
    counterparty: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX))
    period: Mapped[str] = mapped_column(String(8), nullable=False, default="once")  # once|week|month|quarter|year
    day: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    purchase_day: Mapped[date | None] = mapped_column(Date)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")  # manual | import | supplier
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlannedOverride(Base):
    """Отдельный платёж плана: перенесён (`day`), со своей суммой (`amount`), пропущен (`skipped`) или оплачен —
    связан с операцией (`op_id`). Ключ платежа (`key`) — источник + план или период + исходная дата, например
    `plan:<plan_id>:2026-11-05`, `tax:usn:tax:2026-07-01`. Изменение — новая запись (правило 12): действует последняя
    по ключу; «вернуть как было» — запись без переноса, суммы и отметок."""

    __tablename__ = "planned_overrides"
    __table_args__ = (Index("ix_planned_overrides_key", "user_id", "key", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    day: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # своя сумма, > 0
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    op_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # оплачено: операция-факт (money_operations.op_id)
    # оплачено без операции — когда «Планирование» не включено (отметка в «Ближайших сроках» налогов)
    paid_day: Mapped[date | None] = mapped_column(Date)
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
