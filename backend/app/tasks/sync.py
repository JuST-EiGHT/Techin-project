"""Celery-задачи синхронизации с Ozon.

Задачи синхронные (Celery), внутри — asyncio.run. Для каждого запуска — свой движок
БД без пула (NullPool): соединения asyncpg привязаны к event loop, а у каждого
asyncio.run он новый.
"""
import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.api_credential import ApiCredential
from app.models.user import User
from app.services.sync.runner import run_sync
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@asynccontextmanager
async def task_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def users_with_active_keys(db: AsyncSession) -> list[uuid.UUID]:
    res = await db.execute(
        select(ApiCredential.user_id)
        .join(User, User.id == ApiCredential.user_id)
        .where(
            User.is_active.is_(True),
            ApiCredential.is_active.is_(True),
            ApiCredential.expires_at > datetime.now(timezone.utc),
        )
        .distinct()
    )
    return list(res.scalars())


async def _sync_for_user(kind: str, user_id: str, run_id: str | None) -> dict:
    async with task_sessions() as sessions:
        run = await run_sync(kind, uuid.UUID(user_id), sessions, uuid.UUID(run_id) if run_id else None)
    if run is None:
        return {"status": "skipped"}
    return {"status": run.status, "stats": run.stats, "error": run.error}


async def _active_users() -> list[uuid.UUID]:
    async with task_sessions() as sessions, sessions() as db:
        return await users_with_active_keys(db)


@celery_app.task(name="tasks.sync_products_for_user")
def sync_products_for_user(user_id: str, run_id: str | None = None) -> dict:
    return asyncio.run(_sync_for_user("products", user_id, run_id))


@celery_app.task(name="tasks.sync_products_all")
def sync_products_all() -> int:
    """По расписанию: отдельная задача на каждого пользователя с действующим ключом —
    сбой у одного не влияет на остальных."""
    queued = 0
    for user_id in asyncio.run(_active_users()):
        try:
            sync_products_for_user.delay(str(user_id))
            queued += 1
        except Exception:  # noqa: BLE001
            log.exception("Не удалось поставить синхронизацию товаров в очередь для %s", user_id)
    log.info("Синхронизация товаров по расписанию: в очереди %d пользователей", queued)
    return queued
