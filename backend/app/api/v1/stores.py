"""Магазины аккаунта (шаг 8.4b): список для переключателя, новый магазин с ключом Ozon, название, отключение.

Это настройки аккаунта (get_current_user), а не магазина: список одинаков, какой бы магазин ни был выбран.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import sync as sync_api
from app.crud import credential as crud_credential
from app.db.session import get_db
from app.models.user import User
from app.schemas.store import StoreCreate, StoreOut, StorePatch
from app.services import stores as svc
from app.services.auth import get_current_user
from app.services.ozon_auth import verify_ozon_credentials
from app.services.sync.runner import SyncAlreadyRunning

router = APIRouter(prefix="/stores", tags=["stores"])

FIRST_LOAD = ("products", "stocks", "promotions", "finance", "balance", "postings")


def _fail(exc: svc.StoreError) -> HTTPException:
    return HTTPException(exc.code, str(exc))


async def _one(db: AsyncSession, account: User, store_id: uuid.UUID) -> StoreOut:
    return next(StoreOut(**s) for s in await svc.overview(db, account) if s["id"] == store_id)


@router.get("", response_model=list[StoreOut])
async def list_stores(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Первый — сам аккаунт, дальше — по дате добавления; отключённые — тоже (их можно вернуть)."""
    return [StoreOut(**s) for s in await svc.overview(db, user)]


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(data: StoreCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Новый магазин под кабинет Ozon: ключ проверяется в Ozon и сохраняется только принятый; сразу начинается загрузка
    товаров, остатков, акций, начислений и выплат за 90 дней — данные нового магазина отдельно от остальных."""
    try:
        await svc.new_store_name(db, user, data.name, data.client_id, data.label)  # проверки — до запроса в Ozon
    except svc.StoreError as exc:
        raise _fail(exc) from exc
    result = await verify_ozon_credentials(data.client_id, data.api_key.get_secret_value())
    if not result.ok:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if result.reason == "unavailable" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, f"Магазин не добавлен: {result.message}")
    try:
        store = await svc.create(db, user, data.name, data.client_id, data.label)
    except svc.StoreError as exc:  # пока шла проверка, кабинет добавили в другой вкладке
        await db.rollback()
        raise _fail(exc) from exc
    await crud_credential.create(db, store.id, data, result.expires_at)  # коммит — вместе с магазином
    for kind in FIRST_LOAD:
        try:
            await sync_api.queue_sync(db, store.id, kind)
        except (SyncAlreadyRunning, sync_api.QueueUnavailable):
            pass  # очередь недоступна — загрузят плановые задачи
    return await _one(db, user, store.id)


@router.patch("/{store_id}", response_model=StoreOut)
async def patch_store(
    store_id: uuid.UUID, data: StorePatch, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Название и включение: отключённый магазин пропадает из переключателя, его данные не загружаются, но
    сохраняются — его можно вернуть."""
    try:
        store = await svc.own(db, user, store_id)
        if data.name is not None:
            await svc.rename(db, user, store, data.name)
        if data.is_active is not None and data.is_active != store.is_active:
            await svc.set_active(db, store, data.is_active)
    except svc.StoreError as exc:
        await db.rollback()
        raise _fail(exc) from exc
    return await _one(db, user, store.id)
