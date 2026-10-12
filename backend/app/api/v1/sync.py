"""Синхронизация с Ozon: ручной запуск и статус."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.db.session import get_db
from app.integrations.ozon.errors import OzonNotConfiguredError
from app.models.sync_run import SUCCESS
from app.models.user import User
from app.schemas.sync import KindStatus, SyncRunResponse, SyncStatusResponse, utc
from app.services.auth import get_current_user
from app.services.sync.runner import SyncAlreadyRunning, active_run, fail_run, latest_run, start_run

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])

QUEUE_UNAVAILABLE = "Фоновые задачи сейчас недоступны, попробуйте позже"


def enqueue_products_sync(user_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Ставит задачу в Celery. Вынесено отдельно — в тестах подменяется."""
    from app.tasks.sync import sync_products_for_user

    sync_products_for_user.delay(str(user_id), str(run_id))


@router.post("/products", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_products_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await crud_credential.has_active(db, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, OzonNotConfiguredError().message)
    try:
        run = await start_run(db, user.id, "products")
    except SyncAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, "Синхронизация уже идёт, дождитесь окончания") from None
    try:
        await run_in_threadpool(enqueue_products_sync, user.id, run.id)
    except Exception:  # noqa: BLE001 — брокер недоступен
        log.exception("Не удалось поставить синхронизацию в очередь")
        await fail_run(db, run, QUEUE_UNAVAILABLE)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, QUEUE_UNAVAILABLE) from None
    return SyncRunResponse.from_model(run)


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await active_run(db, user.id, "products")  # помечает зависшие запуски проваленными
    last = await latest_run(db, user.id, "products")
    success = await latest_run(db, user.id, "products", status=SUCCESS)
    return SyncStatusResponse(
        has_active_key=await crud_credential.has_active(db, user.id),
        products=KindStatus(
            last_run=SyncRunResponse.from_model(last) if last else None,
            last_success_at=utc(success.finished_at) if success else None,
        ),
    )
