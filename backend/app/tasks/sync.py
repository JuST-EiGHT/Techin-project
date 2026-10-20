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
    from app.services.pnl_cache import cache as pnl_cache

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        await pnl_cache.aclose()  # клиент Redis кэша P&L привязан к циклу этого запуска


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


async def _sync_for_user(kind: str, user_id: str, run_id: str | None, params: dict | None = None) -> dict:
    async with task_sessions() as sessions:
        run = await run_sync(kind, uuid.UUID(user_id), sessions, uuid.UUID(run_id) if run_id else None, params)
    if run is None:
        return {"status": "skipped"}
    return {"status": run.status, "stats": run.stats, "error": run.error}


async def _active_users() -> list[uuid.UUID]:
    async with task_sessions() as sessions, sessions() as db:
        return await users_with_active_keys(db)


def _fan_out(kind: str, task) -> int:
    """Отдельная задача на каждого пользователя с действующим ключом — сбой у одного не влияет на остальных."""
    queued = 0
    for user_id in asyncio.run(_active_users()):
        try:
            task.delay(str(user_id))
            queued += 1
        except Exception:  # noqa: BLE001
            log.exception("Не удалось поставить синхронизацию %s в очередь для %s", kind, user_id)
    log.info("Синхронизация %s по расписанию: в очереди %d пользователей", kind, queued)
    return queued


@celery_app.task(name="tasks.sync_products_for_user")
def sync_products_for_user(user_id: str, run_id: str | None = None) -> dict:
    return asyncio.run(_sync_for_user("products", user_id, run_id))


@celery_app.task(name="tasks.sync_products_all")
def sync_products_all() -> int:
    return _fan_out("products", sync_products_for_user)


@celery_app.task(name="tasks.sync_stocks_for_user")
def sync_stocks_for_user(user_id: str, run_id: str | None = None) -> dict:
    return asyncio.run(_sync_for_user("stocks", user_id, run_id))


@celery_app.task(name="tasks.sync_stocks_all")
def sync_stocks_all() -> int:
    return _fan_out("stocks", sync_stocks_for_user)


@celery_app.task(name="tasks.sync_finance_for_user")
def sync_finance_for_user(
    user_id: str, run_id: str | None = None, date_from: str | None = None, date_to: str | None = None
) -> dict:
    """Без периода: вчера, сегодня и незагруженные дни за 90 дней; с периодом — он заново."""
    params = {"date_from": date_from, "date_to": date_to} if date_from and date_to else None
    return asyncio.run(_sync_for_user("finance", user_id, run_id, params))


@celery_app.task(name="tasks.sync_finance_all")
def sync_finance_all() -> int:
    return _fan_out("finance", sync_finance_for_user)


@celery_app.task(name="tasks.sync_promotions_for_user")
def sync_promotions_for_user(user_id: str, run_id: str | None = None) -> dict:
    return asyncio.run(_sync_for_user("promotions", user_id, run_id))


@celery_app.task(name="tasks.sync_promotions_all")
def sync_promotions_all() -> int:
    return _fan_out("promotions", sync_promotions_for_user)


@celery_app.task(name="tasks.sync_balance_for_user")
def sync_balance_for_user(
    user_id: str, run_id: str | None = None, date_from: str | None = None, date_to: str | None = None
) -> dict:
    """Баланс у Ozon по дням (выплаты): без периода — вчера, сегодня и незагруженные дни за 90 дней."""
    params = {"date_from": date_from, "date_to": date_to} if date_from and date_to else None
    return asyncio.run(_sync_for_user("balance", user_id, run_id, params))


@celery_app.task(name="tasks.sync_balance_all")
def sync_balance_all() -> int:
    return _fan_out("balance", sync_balance_for_user)


@celery_app.task(name="tasks.sync_bank_for_user")
def sync_bank_for_user(user_id: str, run_id: str | None = None, connection_id: str | None = None) -> dict:
    """Банки по API (шаг 3.12): все подключения пользователя или одно — новые операции и остатки."""
    params = {"connection_id": connection_id} if connection_id else None
    return asyncio.run(_sync_for_user("bank", user_id, run_id, params))


@celery_app.task(name="tasks.sync_postings_for_user")
def sync_postings_for_user(user_id: str, run_id: str | None = None) -> dict:
    """Отправления FBO и FBS (шаг 5.2): первый раз — за 90 дней, дальше — последние 35 дней."""
    return asyncio.run(_sync_for_user("postings", user_id, run_id))


@celery_app.task(name="tasks.sync_postings_all")
def sync_postings_all() -> int:
    return _fan_out("postings", sync_postings_for_user)


async def _bank_users() -> list[uuid.UUID]:
    from app.services.bank_connections import users_with_connections

    async with task_sessions() as sessions, sessions() as db:
        return await users_with_connections(db)


@celery_app.task(name="tasks.sync_bank_all")
def sync_bank_all() -> int:
    """По расписанию — пользователям с подключённым банком (ключ Ozon для этого не нужен)."""
    queued = 0
    for user_id in asyncio.run(_bank_users()):
        try:
            sync_bank_for_user.delay(str(user_id))
            queued += 1
        except Exception:  # noqa: BLE001
            log.exception("Не удалось поставить обновление банка в очередь для %s", user_id)
    return queued


@celery_app.task(name="tasks.detect_anomalies_for_user")
def detect_anomalies_for_user(user_id: str, run_id: str | None = None) -> dict:
    """Поиск аномалий (шаг 7.1): новые — записи, прошедшие — закрываются."""
    return asyncio.run(_sync_for_user("anomalies", user_id, run_id))


async def users_for_anomalies(db: AsyncSession) -> list[uuid.UUID]:
    """Кому искать аномалии: действующий ключ Ozon или включённое «Планирование» (денежные проверки — без Ozon)."""
    from app.models.planning import PlanningSettings

    planning = await db.execute(
        select(PlanningSettings.user_id).join(User, User.id == PlanningSettings.user_id)
        .where(User.is_active.is_(True), PlanningSettings.enabled.is_(True))
    )
    return sorted(set(await users_with_active_keys(db)) | set(planning.scalars()), key=str)


async def _anomaly_users() -> list[uuid.UUID]:
    async with task_sessions() as sessions, sessions() as db:
        return await users_for_anomalies(db)


@celery_app.task(name="tasks.detect_anomalies_all")
def detect_anomalies_all() -> int:
    """По расписанию, раз в час — каждому пользователю своя задача: сбой у одного не влияет на остальных."""
    queued = 0
    for user_id in asyncio.run(_anomaly_users()):
        try:
            detect_anomalies_for_user.delay(str(user_id))
            queued += 1
        except Exception:  # noqa: BLE001
            log.exception("Не удалось поставить поиск аномалий в очередь для %s", user_id)
    return queued


async def _send_notifications() -> dict:
    from app.services.notifier import send_due

    async with task_sessions() as sessions, sessions() as db:
        return await send_due(db)


@celery_app.task(name="tasks.send_notifications")
def send_notifications() -> dict:
    """Уведомления в Telegram, которым пора (шаг 7.3): закончились тихие часы, повтор после сбоя сети или лимита
    Telegram. Новые аномалии уходят сразу после поиска — здесь только отложенные."""
    return asyncio.run(_send_notifications())


# Задача Celery для каждого вида синхронизации — для ручного запуска из API
FOR_USER_TASKS = {
    "products": sync_products_for_user,
    "stocks": sync_stocks_for_user,
    "finance": sync_finance_for_user,
    "promotions": sync_promotions_for_user,
    "balance": sync_balance_for_user,
    "bank": sync_bank_for_user,
    "postings": sync_postings_for_user,
    "anomalies": detect_anomalies_for_user,
}
