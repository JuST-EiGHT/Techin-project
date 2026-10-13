import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROCESSING, READY, ERROR = "processing", "ready", "error"
DEFAULT_VAT_MULTIPLIER = Decimal("1.22")
DEFAULT_TAX_RATE = Decimal("0.20")


class Report(Base):
    """Загруженный XLSX-отчёт Ozon: итоги разбора и ручные правки пользователя.

    Удаление — скрытие (deleted_at): запись и исходный файл остаются (правило 12, docs/DATA_HISTORY.md),
    строки начислений отчёта удаляются — они пересобираются из файла при восстановлении.
    """

    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_user_type_period", "user_id", "report_type", "period_start"),
        Index("ix_reports_user_sha256", "user_id", "file_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)  # accrual | realization
    period: Mapped[str] = mapped_column(String(100), nullable=False, default="")  # как в отчёте
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    # ready — разобран; error — последнее перечитывание файла не удалось (итоги прежние);
    # processing — разбор идёт (сейчас разбор синхронный, состояние на будущее)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=READY)
    error_message: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False)  # NormalizedReportData
    manual_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # «сколько реально пришло»
    vat_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False, default=DEFAULT_VAT_MULTIPLIER)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=DEFAULT_TAX_RATE)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("reports.id", ondelete="SET NULL"))


class ReportFile(Base):
    """Исходный файл отчёта — отдельно, чтобы списки отчётов не тянули содержимое файлов."""

    __tablename__ = "report_files"

    report_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
