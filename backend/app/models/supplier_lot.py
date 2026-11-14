"""Поставки от поставщика и оплаты ему (шаг 10.23a «Долги поставщикам», оплата после продажи).

Поставка (партия) — документ поставщика: № и дата, поставщик, строки «артикул × количество». Продажи Ozon сервис сам
относит к поставкам — FIFO внутри артикула (engine/supplier_lots); долг по поставке = продано × цену из справочника
себестоимости на дату поставки с НДС поставщика. Цены в документе нет — её не храним, она производная.

Правило 12: исходные данные не перезаписываются.
- Поставка — версии (`lot_id` тот же, `version` + 1, прежняя — `superseded_at`); у каждой версии свои строки.
  Повторная загрузка того же документа с другим составом, правка «уже продано ранее», закрытие — новая версия.
  Удаление — `deleted_at` у действующей версии.
- Файл документа хранится как есть (`supplier_documents`) — его можно скачать и перечитать.
- Оплата, внесённая здесь, — версии так же (`payment_id`). Оплаты из «Операций» (статья «Оплата поставщику») здесь
  не хранятся — они засчитываются сами.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LOT_SOURCES = ("pdf", "xlsx", "csv", "manual")
CLOSE_REASONS = ("returned", "written_off", "other")  # вернули поставщику, списали, другое
COMMENT_MAX = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SupplierDocument(Base):
    """Загруженный файл документа поставщика — как есть (правило 12)."""

    __tablename__ = "supplier_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now,
                                                  server_default=func.now())


class SupplierLot(Base):
    __tablename__ = "supplier_lots"
    __table_args__ = (Index("ix_supplier_lots_user_lot", "user_id", "lot_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[str] = mapped_column(String(64), nullable=False)  # № отгрузки / накладной
    day: Mapped[date] = mapped_column(Date, nullable=False)  # дата документа: продажи раньше неё в поставку не идут
    supplier: Mapped[str | None] = mapped_column(String(300))  # «Организация» документа
    receiver: Mapped[str | None] = mapped_column(String(300))  # «Контрагент» — только показываем
    warehouse: Mapped[str | None] = mapped_column(String(200))  # «Склад» поставщика — только показываем
    doc_total_qty: Mapped[int | None] = mapped_column(Integer)  # «Общее количество» в документе — для сверки
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="pdf")  # LOT_SOURCES
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("supplier_documents.id", ondelete="SET NULL")
    )
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX))
    # закрыта вручную с остатком (вернули поставщику, списали): остаток — в written_off строк этой версии
    closed_on: Mapped[date | None] = mapped_column(Date)
    close_reason: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now,
                                                 server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierLotLine(Base):
    """Строка версии поставки: артикул как в документе (сопоставление с товаром — только по нему) и количество."""

    __tablename__ = "supplier_lot_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lot_row_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_lots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300))
    barcode: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    # продано до того, как сервис видит продажи (старая поставка): входит в долг, но не в распределение продаж
    sold_before: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    written_off: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # при закрытии


class SupplierPayment(Base):
    """Оплата поставщику, внесённая на странице «Долги поставщикам» (версии — как у поставки)."""

    __tablename__ = "supplier_payments"
    __table_args__ = (Index("ix_supplier_payments_user_day", "user_id", "day"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now,
                                                 server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
