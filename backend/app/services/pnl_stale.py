"""Сброс производных итогов P&L (шаг 3.2, docs/DATA_HISTORY.md «Пересчёт сохранённых итогов»).

Вызывается там, где меняются исходные данные: отчёты, себестоимость, налоговый профиль, начисления из API.
Отдельный модуль без зависимостей от расчёта — чтобы его могли импортировать и синхронизации.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pnl_snapshot import PnLSnapshot
from app.services.pnl_cache import cache


def affected_period(dates: list[date], day: date) -> tuple[date | None, date | None]:
    """Где действует новая запись истории с датой day (себестоимость артикула, налоговый профиль), если до неё
    были записи с датами dates: с day до следующей записи; первая запись действует и на даты раньше себя."""
    later = [d for d in dates if d > day]
    return (day if any(d <= day for d in dates) else None, min(later) - timedelta(days=1) if later else None)


async def invalidate(
    db: AsyncSession, user_id: uuid.UUID, date_from: date | None, date_to: date | None, reason: str
) -> None:
    """Данные за [date_from, date_to] изменились (None — без границы): снимки этого периода — устаревшие,
    кэш пользователя сброшен. Коммит — на вызывающем (снимки помечаются в той же транзакции, что и данные)."""
    stmt = update(PnLSnapshot).where(PnLSnapshot.user_id == user_id, PnLSnapshot.stale.is_(False))
    if date_from is not None:
        stmt = stmt.where(PnLSnapshot.date_to >= date_from)
    if date_to is not None:
        stmt = stmt.where(PnLSnapshot.date_from <= date_to)
    await db.execute(
        stmt.values(stale=True, stale_reason=reason[:255], stale_since=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    await cache.invalidate(user_id)
