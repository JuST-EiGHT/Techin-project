import uuid
from datetime import datetime, timezone

from pydantic import BaseModel

from app.models.sync_run import SyncRun


def utc(dt: datetime | None) -> datetime | None:
    # SQLite (тесты) отдаёт даты без часового пояса; храним всегда UTC
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SyncRunResponse(BaseModel):
    id: uuid.UUID
    kind: str
    status: str  # queued | running | success | failed
    started_at: datetime
    finished_at: datetime | None
    stats: dict | None
    error: str | None

    @classmethod
    def from_model(cls, r: SyncRun) -> "SyncRunResponse":
        return cls(
            id=r.id, kind=r.kind, status=r.status, started_at=utc(r.started_at),
            finished_at=utc(r.finished_at), stats=r.stats, error=r.error,
        )


class KindStatus(BaseModel):
    last_run: SyncRunResponse | None
    last_success_at: datetime | None  # для «Обновлено N минут назад»


class SyncStatusResponse(BaseModel):
    has_active_key: bool
    products: KindStatus
    stocks: KindStatus
    finance: KindStatus
    promotions: KindStatus
