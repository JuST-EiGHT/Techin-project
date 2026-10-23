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
FILE, API = "file", "api"  # откуда отчёт: загружен файлом XLSX или получен через Ozon API (шаг 10.12)


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
    # налог и НДС — по налоговому профилю на дату периода (шаг 2.5); значение — ручная поправка для
    # этого отчёта: свой коэффициент НДС поставщика и «свой процент» от прибыли или дохода
    vat_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    tax_base: Mapped[str | None] = mapped_column(String(8))  # profit | revenue
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("reports.id", ondelete="SET NULL"))
    # file — загружен XLSX; api — получен через Ozon API, в report_files — исходный ответ JSON (шаг 10.12)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default=FILE, server_default=FILE)


class ReportFile(Base):
    """Исходный файл отчёта — отдельно, чтобы списки отчётов не тянули содержимое файлов."""

    __tablename__ = "report_files"

    report_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


# документы Ozon, которые формируются по запросу и приходят файлом (шаг 10.12)
REQUESTED, DOC_READY, DOC_ERROR = "requested", "ready", "error"


class OzonDocument(Base):
    """Документ Ozon, полученный через API файлом: отчёт о взаиморасчётах, компенсациях, декомпенсациях.

    Ozon формирует его по запросу: ответ — код, по коду (`/v1/report/info`) — статус и ссылка на файл, файл скачиваем
    и храним (правило 12). Данные из файла сервис пока не разбирает — документ для бухгалтерии и сверки. Записи не
    удаляются: повторный запрос — новая запись.
    """

    __tablename__ = "ozon_documents"
    __table_args__ = (Index("ix_ozon_documents_user_requested", "user_id", "requested_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)  # mutual_settlement | compensation | decompensation
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # requested | ready | error
    ozon_code: Mapped[str | None] = mapped_column(String(255))  # код отчёта у Ozon (живёт 3 дня)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # последний опрос /v1/report/info
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)  # простыми словами
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    info_json: Mapped[dict | None] = mapped_column(JSON)  # последний ответ /v1/report/info как есть
