"""Запуск синхронизаций с журналом в sync_runs.

Каждый запуск — строка SyncRun: queued → running → success/failed. Одновременно у
пользователя может идти только один запуск каждого вида; «зависший» (воркер упал)
запуск через STALE_AFTER считается проваленным и не блокирует новые.
Ошибки Ozon и любые другие записываются в run.error и наружу не пробрасываются:
сбой у одного пользователя не мешает остальным.
"""
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import OzonAPIError
from app.integrations.ozon.factory import get_ozon_client
from app.models.sync_run import ACTIVE_STATUSES, FAILED, QUEUED, RUNNING, SUCCESS, SyncRun
from app.services.sync.products import sync_products
from app.services.sync.finance import sync_finance
from app.services.sync.stocks import sync_stocks

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=30)
INTERNAL_ERROR = "Внутренняя ошибка синхронизации, попробуйте позже"

Job = Callable[..., Awaitable[Any]]  # (db, user_id, client, **params) -> stats


async def _products_job(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> dict:
    return (await sync_products(db, user_id, client)).as_dict()


async def _stocks_job(db: AsyncSession, user_id: uuid.UUID, client: OzonClient) -> dict:
    return (await sync_stocks(db, user_id, client)).as_dict()


async def _finance_job(
    db: AsyncSession, user_id: uuid.UUID, client: OzonClient, date_from: str | None = None, date_to: str | None = None
) -> dict:
    period = (date.fromisoformat(date_from), date.fromisoformat(date_to)) if date_from and date_to else (None, None)
    return (await sync_finance(db, user_id, client, *period)).as_dict()


JOBS: dict[str, Job] = {"products": _products_job, "stocks": _stocks_job, "finance": _finance_job}


class SyncAlreadyRunning(Exception):
    pass


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def latest_run(db: AsyncSession, user_id: uuid.UUID, kind: str, status: str | None = None) -> SyncRun | None:
    q = select(SyncRun).where(SyncRun.user_id == user_id, SyncRun.kind == kind)
    if status:
        q = q.where(SyncRun.status == status)
    res = await db.execute(q.order_by(SyncRun.started_at.desc()).limit(1))
    return res.scalar_one_or_none()


async def active_run(db: AsyncSession, user_id: uuid.UUID, kind: str) -> SyncRun | None:
    run = await latest_run(db, user_id, kind)
    if run is None or run.status not in ACTIVE_STATUSES:
        return None
    if datetime.now(timezone.utc) - _utc(run.started_at) > STALE_AFTER:
        run.status, run.error = FAILED, "Синхронизация прервалась, запустите её снова"
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return None
    return run


async def start_run(db: AsyncSession, user_id: uuid.UUID, kind: str, status: str = QUEUED) -> SyncRun:
    if await active_run(db, user_id, kind):
        raise SyncAlreadyRunning
    run = SyncRun(user_id=user_id, kind=kind, status=status, started_at=datetime.now(timezone.utc))
    db.add(run)
    await db.commit()
    return run


async def fail_run(db: AsyncSession, run: SyncRun, error: str) -> None:
    run.status, run.error, run.finished_at = FAILED, error, datetime.now(timezone.utc)
    await db.commit()


async def run_sync(
    kind: str,
    user_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID | None = None,
    params: dict[str, Any] | None = None,
) -> SyncRun | None:
    """Выполняет синхронизацию вида kind. run_id — запуск, созданный API (ручной);
    без него (по расписанию) запуск создаётся здесь. params — аргументы задачи
    (например, период начислений). Возвращает None, если запуск пропущен."""
    job = JOBS[kind]
    async with session_factory() as db:
        if run_id is not None:
            run = await db.get(SyncRun, run_id)
            if run is None or run.status not in ACTIVE_STATUSES:
                log.warning("Запуск синхронизации %s не найден или уже завершён", run_id)
                return None
        else:
            try:
                run = await start_run(db, user_id, kind, status=RUNNING)
            except SyncAlreadyRunning:
                log.info("Синхронизация %s у пользователя %s уже идёт — пропускаем", kind, user_id)
                return None

        run.status = RUNNING
        await db.commit()
        try:
            async with await get_ozon_client(user_id, db) as client:
                stats = await job(db, user_id, client, **(params or {}))
        except OzonAPIError as e:
            await db.rollback()
            log.warning("Синхронизация %s у пользователя %s: %s (%s)", kind, user_id, e.message, e.ozon_message)
            await fail_run(db, run, e.message)
            return run
        except Exception:  # noqa: BLE001 — любая ошибка одного пользователя не должна ронять остальных
            await db.rollback()
            log.exception("Синхронизация %s у пользователя %s упала", kind, user_id)
            await fail_run(db, run, INTERNAL_ERROR)
            return run

        run.status, run.stats, run.finished_at = SUCCESS, stats, datetime.now(timezone.utc)
        await db.commit()
        return run
