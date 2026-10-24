"""API-ключи Ozon пользователя. Api-Key принимается, но никогда не возвращается."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import sync as sync_api
from app.crud import credential as crud
from app.db.session import get_db
from app.models.api_credential import ApiCredential
from app.models.user import User
from app.schemas.credential import CredentialCreate, CredentialResponse, VerifyResponse
from app.services.stores import get_current_store
from app.services.encryption import DecryptionError, decrypt
from app.services.ozon_auth import verify_ozon_credentials
from app.services import stores
from app.services.sync.runner import SyncAlreadyRunning

router = APIRouter(prefix="/credentials", tags=["credentials"])

_NOT_FOUND = "Ключ не найден"


async def _own(cred_id: uuid.UUID, user: User, db: AsyncSession) -> ApiCredential:
    cred = await crud.get_for_user(db, user.id, cred_id)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return cred


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
async def add_credential(data: CredentialCreate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    # Один магазин — один кабинет Ozon (шаг 8.4b): ключ другого кабинета — только отдельным магазином
    try:
        await stores.check_key_for_store(db, await stores.account_of(db, user), user, data.client_id)
    except stores.StoreError as exc:
        raise HTTPException(exc.code, str(exc)) from exc
    # Сохраняем только ключ, который Ozon принял
    result = await verify_ozon_credentials(data.client_id, data.api_key.get_secret_value())
    if not result.ok:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if result.reason == "unavailable" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, f"Ключ не сохранён: {result.message}")
    stores.bind_client(user, data.client_id)
    cred = await crud.create(db, user.id, data, result.expires_at)
    await _start_finance_backfill(db, user.id)
    return CredentialResponse.from_model(cred)


async def _start_finance_backfill(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Ключ подключён — разово загружаем начисления, баланс у Ozon (выплаты) и отправления (поставки, шаг 5.2) за
    последние 90 дней (уже загруженные дни начислений пропускаются) и заявки на поставку (10.18). Сбой очереди не мешает
    сохранить ключ: догрузят плановые задачи по расписанию."""
    for kind in ("finance", "balance", "postings", "supplies"):
        try:
            await sync_api.queue_sync(db, user_id, kind)
        except (SyncAlreadyRunning, sync_api.QueueUnavailable):
            pass


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return [CredentialResponse.from_model(c) for c in await crud.list_for_user(db, user.id)]


@router.delete("/{cred_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(cred_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    await crud.delete(db, await _own(cred_id, user, db))


@router.post("/{cred_id}/verify", response_model=VerifyResponse)
async def verify_credential(cred_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    cred = await _own(cred_id, user, db)
    try:
        api_key = decrypt(cred.api_key_encrypted)
    except DecryptionError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"{e}. Удалите ключ и добавьте заново") from e

    result = await verify_ozon_credentials(cred.client_id, api_key)
    if result.ok:
        cred.is_active = True
        cred.last_used_at = datetime.now(timezone.utc)
        if result.expires_at:
            cred.expires_at = result.expires_at  # срок по данным Ozon точнее введённого
    elif result.reason in ("invalid", "forbidden"):
        cred.is_active = False
    # unavailable — состояние ключа неизвестно, ничего не меняем
    cred = await crud.save(db, cred)
    return VerifyResponse(ok=result.ok, message=result.message, credential=CredentialResponse.from_model(cred))
