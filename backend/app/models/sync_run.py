import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

QUEUED, RUNNING, SUCCESS, FAILED = "queued", "running", "success", "failed"
ACTIVE_STATUSES = (QUEUED, RUNNING)


class SyncRun(Base):
    """Журнал запусков синхронизации с Ozon: для статуса в интерфейсе и защиты от двойного запуска."""

    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_user_kind_started", "user_id", "kind", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "products", дальше — "stocks" и т.д.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
