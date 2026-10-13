import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PnLSnapshot(Base):
    """Сохранённый P&L закрытого месяца (шаг 3.2) — для быстрого дашборда.

    Производный итог (правило 12): пересчитывается из исходных данных. Помечается устаревшим, если в
    его периоде изменились себестоимость, налоговый профиль, отчёты или начисления
    (docs/DATA_HISTORY.md, «Пересчёт сохранённых итогов»); пересчёт — при следующем запросе.
    """

    __tablename__ = "pnl_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "date_from", "date_to", "source", name="uq_pnl_snapshots_period"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # запрошенный: auto | xlsx | api
    data_json: Mapped[dict] = mapped_column(JSON, nullable=False)  # ответ GET /pnl
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(255))
    stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
