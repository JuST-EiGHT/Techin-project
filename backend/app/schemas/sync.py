import uuid
from datetime import datetime, timezone
from typing import Literal

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


class SyncAction(BaseModel):
    label: str
    href: str


class KindOverview(BaseModel):
    """Вид данных в окне «Синхронизация» (шаг 10.2)."""

    kind: str
    title: str  # «Товары и цены», «Остатки» …
    state: str  # fresh | running | error | stale | never
    running: bool
    last_success_at: datetime | None
    schedule: str  # «сами — раз в час»
    problem: str | None  # что случилось — простыми словами
    hint: str | None  # что сделать
    action: SyncAction | None  # куда перейти (ключ истёк — «Интеграции»)
    error: str | None  # текст последней ошибки — подробности (без ключей и токенов)
    recent_until: datetime | None  # до этого времени «Обновить» не нужно — данные только что загружены
    can_refresh: bool  # False — не обновить, пока не исправят (нет ключа, банку нужен новый токен): только action


class SyncStatusResponse(BaseModel):
    has_active_key: bool
    products: KindStatus
    stocks: KindStatus
    finance: KindStatus
    promotions: KindStatus
    balance: KindStatus  # баланс у Ozon и выплаты (шаг 3.6)
    has_bank: bool = False  # подключён банк по API (шаг 3.12) — ключ Ozon для этого не нужен
    bank: KindStatus | None = None
    postings: KindStatus | None = None  # отправления FBO и FBS (шаг 5.2)
    # шаг 10.2: общее состояние для кнопки «Синхронизация» — fresh | running | error | stale | no_source (нечего загружать)
    state: str = "no_source"
    oldest_kind: str | None = None  # самые старые данные: вид…
    oldest_success_at: datetime | None = None  # …и когда обновлён (None — ещё не загружался)
    kinds: list[KindOverview] = []  # виды магазина: Ozon — если подключали ключ, банк — если подключён


class SyncAllRequest(BaseModel):
    kinds: list[str] | None = None  # None — все виды магазина; один вид — «Обновить» в его строке


class SyncAllItem(BaseModel):
    kind: str
    title: str
    result: Literal["queued", "running", "recent", "unavailable"]
    recent_until: datetime | None = None  # «recent»: снова можно после
    run_id: uuid.UUID | None = None


class SyncAllResponse(BaseModel):
    items: list[SyncAllItem]
    message: str  # итог одной фразой: что поставлено, что уже идёт, что обновлено недавно
