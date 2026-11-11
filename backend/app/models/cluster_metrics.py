"""Данные Ozon по кластерам для планирования поставок (шаг 10.18a).

`cluster_stock_metrics` — что Ozon сам считает по товару в кластере («FBO → Управление остатками»,
`/v1/analytics/stocks`): продажи в день за 28 дней, на сколько дней хватит, дни без продаж, статус ликвидности.
`ozon_supply_recs` — рекомендованная поставка Ozon по товару и кластеру («Аналитика → Планирование поставок →
Локальность продаж», `/v1/analytics/local-sale/items-clusters/info`, бета).

Исходные данные (правило 12): новая запись — только когда цифры изменились; пара пропала из рекомендаций — нулевая
запись. Действует последняя по паре.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClusterStockMetric(Base):
    __tablename__ = "cluster_stock_metrics"
    __table_args__ = (Index("ix_cluster_stock_metrics_pair", "product_id", "cluster_id", "snapshot_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    cluster_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # кластер Ozon (`cluster_id`)
    macrolocal_cluster_id: Mapped[int | None] = mapped_column(BigInteger)  # кластер размещения — в него принимают поставку
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    ads: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))  # продажи в день в кластере за 28 дней (`ads_cluster`)
    idc: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))  # на сколько дней хватит остатка в кластере (`idc_cluster`)
    days_without_sales: Mapped[int | None] = mapped_column(Integer)  # `days_without_sales_cluster`
    turnover_grade: Mapped[str | None] = mapped_column(String(32))  # `turnover_grade_cluster`: DEFICIT, POPULAR …
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OzonSupplyRec(Base):
    __tablename__ = "ozon_supply_recs"
    __table_args__ = (Index("ix_ozon_supply_recs_pair", "user_id", "sku", "macrolocal_cluster_id", "snapshot_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    sku: Mapped[int] = mapped_column(BigInteger, nullable=False)
    macrolocal_cluster_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # кластер покупателя
    cluster_name: Mapped[str | None] = mapped_column(String(255))  # из /v1/cluster/list
    recommended: Mapped[int] = mapped_column(Integer, nullable=False)  # `recommended_supply`, шт.
    attention_level: Mapped[str | None] = mapped_column(String(16))  # LOW | MEDIUM | HIGH …
    reasons: Mapped[list | None] = mapped_column(JSON)  # [{reason, quantity, amount}] — почему переплата за логистику
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OzonSupplyRecSync(Base):
    """Когда рекомендации Ozon загружались и получилось ли: без этого «Ozon ничего не советует» не отличить от «не
    загружали» (метод бета — может не ответить, тогда расчёт идёт по продажам)."""

    __tablename__ = "ozon_supply_rec_syncs"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # последняя удачная загрузка
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
