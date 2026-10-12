"""API-ключи Ozon пользователя. Api-Key принимается, но никогда не возвращается."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud
from app.db.session import get_db
from app.models.api_credential import ApiCredential
from app.models.user import User
from app.schemas.credential import CredentialCreate, CredentialResponse, VerifyResponse
from app.services.auth import get_current_user
from app.services.encryption import DecryptionError, decrypt
from app.services.ozon_auth import verify_ozon_credentials

router = APIRouter(prefix="/credentials", tags=["credentials"])

_NOT_FOUND = "Ключ не найден"


async def _own(cred_id: uuid.UUID, user: User, db: AsyncSession) -> ApiCredential:
    cred = await crud.get_for_user(db, user.id, cred_id)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return cred


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
async def add_credential(data: CredentialCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Сохраняем только ключ, который Ozon принял
    result = await verify_ozon_credentials(data.client_id, data.api_key.get_secret_value())
    if not result.ok:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if result.reason == "unavailable" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, f"Ключ не сохранён: {result.message}")
    return CredentialResponse.from_model(await crud.create(db, user.id, data, result.expires_at))


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [CredentialResponse.from_model(c) for c in await crud.list_for_user(db, user.id)]


@router.delete("/{cred_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(cred_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await crud.delete(db, await _own(cred_id, user, db))


@router.post("/{cred_id}/verify", response_model=VerifyResponse)
async def verify_credential(cred_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
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
