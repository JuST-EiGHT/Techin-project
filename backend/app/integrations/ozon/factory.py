"""Клиент Ozon для пользователя по его сохранённому API-ключу."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient, mask_client_id
from app.integrations.ozon.errors import OzonNotConfiguredError
from app.models.api_credential import ApiCredential
from app.services.encryption import DecryptionError, decrypt

log = logging.getLogger(__name__)


async def get_ozon_client(user_id: uuid.UUID, db: AsyncSession) -> OzonClient:
    """Берёт действующий ключ пользователя (активный и не истёкший), расшифровывает,
    отмечает last_used_at. Если ключей несколько — последний использованный.

    Бросает OzonNotConfiguredError, если рабочего ключа нет. Клиент нужно закрыть
    (async with / aclose).
    """
    now = datetime.now(timezone.utc)
    res = await db.execute(
        select(ApiCredential)
        .where(ApiCredential.user_id == user_id, ApiCredential.is_active.is_(True), ApiCredential.expires_at > now)
        .order_by(ApiCredential.last_used_at.desc().nulls_last(), ApiCredential.created_at.desc())
        .limit(1)
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        raise OzonNotConfiguredError()

    try:
        api_key = decrypt(cred.api_key_encrypted)
    except DecryptionError:
        log.error("Ключ Ozon %s (client %s) не расшифровывается текущим ENCRYPTION_KEY", cred.id, mask_client_id(cred.client_id))
        raise OzonNotConfiguredError(
            "Сохранённый API-ключ Ozon не расшифровывается — удалите его и добавьте заново"
        ) from None

    cred.last_used_at = now
    await db.commit()
    return OzonClient(cred.client_id, api_key)
