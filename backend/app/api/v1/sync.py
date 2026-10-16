"""Синхронизация с Ozon: ручной запуск и статус."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.db.session import get_db
from app.integrations.ozon.errors import OzonNotConfiguredError
from app.models.sync_run import SUCCESS, SyncRun
from app.models.user import User
from app.schemas.sync import KindStatus, SyncRunResponse, SyncStatusResponse, utc
from app.services import bank_connections
from app.services.auth import get_current_user
from app.services.sync.runner import SyncAlreadyRunning, active_run, fail_run, latest_run, start_run

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])

QUEUE_UNAVAILABLE = "Фоновые задачи сейчас недоступны, попробуйте позже"


def enqueue_sync(kind: str, user_id: uuid.UUID, run_id: uuid.UUID, params: dict | None = None) -> None:
    """Ставит задачу в Celery. Вынесено отдельно — в тестах подменяется."""
    from app.tasks.sync import FOR_USER_TASKS

    FOR_USER_TASKS[kind].delay(str(user_id), str(run_id), **(params or {}))


class QueueUnavailable(Exception):
    pass


async def queue_sync(db: AsyncSession, user_id: uuid.UUID, kind: str, params: dict | None = None) -> SyncRun:
    """Создаёт запуск и ставит задачу в очередь. SyncAlreadyRunning — уже идёт; QueueUnavailable — нет брокера."""
    run = await start_run(db, user_id, kind)
    try:
        await run_in_threadpool(enqueue_sync, kind, user_id, run.id, params)
    except Exception as e:  # noqa: BLE001 — брокер недоступен
        log.exception("Не удалось поставить синхронизацию %s в очередь", kind)
        await fail_run(db, run, QUEUE_UNAVAILABLE)
        raise QueueUnavailable from e
    return run


async def _sync_now(kind: str, user: User, db: AsyncSession, params: dict | None = None) -> SyncRunResponse:
    if not await crud_credential.has_active(db, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, OzonNotConfiguredError().message)
    try:
        run = await queue_sync(db, user.id, kind, params)
    except SyncAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, "Синхронизация уже идёт, дождитесь окончания") from None
    except QueueUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, QUEUE_UNAVAILABLE) from None
    return SyncRunResponse.from_model(run)


@router.post("/products", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_products_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _sync_now("products", user, db)


@router.post("/stocks", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_stocks_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _sync_now("stocks", user, db)


@router.post("/balance", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_balance_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Баланс у Ozon и выплаты: вчера, сегодня и незагруженные дни за 90 дней."""
    return await _sync_now("balance", user, db)


@router.post("/promotions", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_promotions_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _sync_now("promotions", user, db)


@router.post("/postings", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_postings_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Отправления FBO и FBS — продажи по кластерам и складам для планирования поставок (шаг 5.2)."""
    return await _sync_now("postings", user, db)


async def _kind_status(db: AsyncSession, user_id: uuid.UUID, kind: str) -> KindStatus:
    await active_run(db, user_id, kind)  # помечает зависшие запуски проваленными
    last = await latest_run(db, user_id, kind)
    success = await latest_run(db, user_id, kind, status=SUCCESS)
    return KindStatus(
        last_run=SyncRunResponse.from_model(last) if last else None,
        last_success_at=utc(success.finished_at) if success else None,
    )


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return SyncStatusResponse(
        has_active_key=await crud_credential.has_active(db, user.id),
        products=await _kind_status(db, user.id, "products"),
        stocks=await _kind_status(db, user.id, "stocks"),
        finance=await _kind_status(db, user.id, "finance"),
        promotions=await _kind_status(db, user.id, "promotions"),
        balance=await _kind_status(db, user.id, "balance"),
        has_bank=bool(await bank_connections.list_connections(db, user.id)),
        bank=await _kind_status(db, user.id, "bank"),
        postings=await _kind_status(db, user.id, "postings"),
    )
