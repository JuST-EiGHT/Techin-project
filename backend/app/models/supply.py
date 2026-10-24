import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# источник строки состава: состав заявки (/v1/supply-order/bundle) или акт приёмки (/v1/supply-order/act/product/get)
BUNDLE, ACT = "bundle", "act"

# план поставки: черновик (меняется) → создана заявка в Ozon (или привязана созданная в кабинете) → отменён
PLAN_DRAFT, PLAN_ORDERED, PLAN_CANCELLED = "draft", "ordered", "cancelled"
PLAN_STATUSES = (PLAN_DRAFT, PLAN_ORDERED, PLAN_CANCELLED)

# черновик заявки в Ozon (/v1/draft/direct/create): создаётся → готов (склады, отказы) → использован или не удался;
# живёт у Ozon 30 минут
DRAFT_PENDING, DRAFT_READY, DRAFT_FAILED, DRAFT_USED = "pending", "ready", "failed", "used"


class SupplyOrder(Base):
    """Заявка на поставку FBO из Ozon (шаг 10.18, `/v3/supply-order/get`). Строка навсегда; последние статус и таймслот —
    здесь же для списка, каждая смена — новая запись в `supply_order_events` (правило 12). `plan_id` — план сервиса,
    из которого заявку создали или к которому её привязали."""

    __tablename__ = "supply_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "ozon_order_id", name="uq_supply_orders_user_order"),
        Index("ix_supply_orders_user_created", "user_id", "created_at_ozon"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ozon_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_number: Mapped[str | None] = mapped_column(String(64))
    created_at_ozon: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeslot_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeslot_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str | None] = mapped_column(String(64))
    dropoff: Mapped[str | None] = mapped_column(String(255))  # пункт отгрузки
    storage_warehouse: Mapped[str | None] = mapped_column(String(255))  # склад хранения (несколько — через запятую)
    cluster_name: Mapped[str | None] = mapped_column(String(255))  # кластер склада хранения
    supplies: Mapped[list | None] = mapped_column(JSON)  # [{supply_id, bundle_id, storage_warehouse_id, cluster_id}]
    raw_json: Mapped[dict | None] = mapped_column(JSON)  # ответ Ozon по заявке (персональных данных в нём нет)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("supply_plans.id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    items_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acts_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplyOrderEvent(Base):
    """Смена статуса или таймслота заявки (только добавление): история поставки."""

    __tablename__ = "supply_order_events"
    __table_args__ = (Index("ix_supply_order_events_order", "order_id", "seen_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("supply_orders.id", ondelete="CASCADE"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # по Ozon
    timeslot_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeslot_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # когда сервис увидел


class SupplyOrderItem(Base):
    """Снимок состава заявки или акта приёмки (только добавление): изменился состав — новый снимок, действует последний
    по источнику."""

    __tablename__ = "supply_order_items"
    __table_args__ = (Index("ix_supply_order_items_order", "order_id", "source", "snapshot_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("supply_orders.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # bundle | act
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supply_id: Mapped[int | None] = mapped_column(BigInteger)
    sku: Mapped[int | None] = mapped_column(BigInteger)
    offer_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(255))
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # заявлено
    fact: Mapped[int | None] = mapped_column(Integer)  # акт: принято фактически
    approved: Mapped[int | None] = mapped_column(Integer)  # акт: согласовано
    defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SupplyPlan(Base):
    """План поставки (шаг 10.18): что и сколько везти в кластер Ozon (или закупить на свой склад) — из рекомендаций, с
    ручной корректировкой; дата — для календаря. Отменённый план остаётся (правило 12)."""

    __tablename__ = "supply_plans"
    __table_args__ = (Index("ix_supply_plans_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # supply | purchase
    destination_key: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_name: Mapped[str] = mapped_column(String(255), nullable=False)
    planned_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=PLAN_DRAFT)
    ozon_order_id: Mapped[int | None] = mapped_column(BigInteger)  # заявка Ozon по этому плану
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupplyPlanItem(Base):
    """Строка плана (версии): ручная корректировка — новая строка, прежняя получает replaced_at; действует строка без
    replaced_at. `recommended` — сколько советовал расчёт, когда товар попал в план."""

    __tablename__ = "supply_plan_items"
    __table_args__ = (Index("ix_supply_plan_items_plan", "plan_id", "product_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("supply_plans.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    recommended: Mapped[int | None] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplyDraft(Base):
    """Черновик заявки в Ozon по плану (правило 6: предпросмотр → подтверждение → журнал): черновик у Ozon невидим в
    кабинете и живёт 30 минут; создание заявки — только по нему, один раз, после подтверждения."""

    __tablename__ = "supply_drafts"
    __table_args__ = (Index("ix_supply_drafts_plan", "plan_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("supply_plans.id", ondelete="CASCADE"), nullable=False)
    ozon_draft_id: Mapped[int | None] = mapped_column(BigInteger)
    cluster_id: Mapped[int | None] = mapped_column(BigInteger)  # macrolocal_cluster_id
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    items_key: Mapped[str] = mapped_column(String(64), nullable=False)  # состав плана при проверке (хэш)
    request_json: Mapped[dict | None] = mapped_column(JSON)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ozon_order_id: Mapped[int | None] = mapped_column(BigInteger)
