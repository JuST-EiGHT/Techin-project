import calendar
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_credential import ApiCredential
from app.schemas.credential import DEFAULT_LABEL, CredentialCreate
from app.services.encryption import encrypt

KEY_LIFETIME_MONTHS = 3  # срок ключа Ozon по документации — если Ozon сам не сообщил expires_at


def add_months(dt: datetime, months: int) -> datetime:
    """Календарные месяцы; 31 августа + 6 мес. → 28/29 февраля."""
    m = dt.month - 1 + months
    year, month = dt.year + m // 12, m % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


async def list_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[ApiCredential]:
    res = await db.execute(
        select(ApiCredential).where(ApiCredential.user_id == user_id).order_by(ApiCredential.created_at)
    )
    return list(res.scalars())


async def get_for_user(db: AsyncSession, user_id: uuid.UUID, cred_id: uuid.UUID) -> ApiCredential | None:
    """Чужой ключ неотличим от несуществующего."""
    cred = await db.get(ApiCredential, cred_id)
    return cred if cred is not None and cred.user_id == user_id else None


async def create(
    db: AsyncSession, user_id: uuid.UUID, data: CredentialCreate, ozon_expires_at: datetime | None = None
) -> ApiCredential:
    """Срок действия: из Ozon (/v1/roles) → введённый пользователем → +3 месяца."""
    now = datetime.now(timezone.utc)
    cred = ApiCredential(
        user_id=user_id,
        label=data.label or DEFAULT_LABEL,
        client_id=data.client_id,
        api_key_encrypted=encrypt(data.api_key.get_secret_value()),
        is_active=True,
        expires_at=ozon_expires_at or data.expires_at or add_months(now, KEY_LIFETIME_MONTHS),
        last_used_at=now,  # ключ только что успешно проверен
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


async def delete(db: AsyncSession, cred: ApiCredential) -> None:
    await db.delete(cred)
    await db.commit()


async def save(db: AsyncSession, cred: ApiCredential) -> ApiCredential:
    await db.commit()
    await db.refresh(cred)
    return cred


async def has_active(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Есть ли у пользователя действующий (активный и не истёкший) ключ — как в integrations/ozon/factory.py."""
    res = await db.execute(
        select(ApiCredential.id)
        .where(
            ApiCredential.user_id == user_id,
            ApiCredential.is_active.is_(True),
            ApiCredential.expires_at > datetime.now(timezone.utc),
        )
        .limit(1)
    )
    return res.first() is not None
