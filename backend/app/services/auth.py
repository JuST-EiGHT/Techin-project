"""Сервис авторизации: пароли, JWT и dependency get_current_user."""
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import user as crud_user
from app.db.session import get_db
from app.models.user import User
from app.services.security import (  # noqa: F401 — реэкспорт
    ACCESS,
    REFRESH,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.services.token_blacklist import blacklist

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}
SESSION_ENDED = "Пароль изменён — войдите снова с новым паролем"


async def get_current_token_payload(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация", headers=_UNAUTHORIZED)
    try:
        payload = decode_token(creds.credentials, expected_type=ACCESS)
    except InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e), headers=_UNAUTHORIZED) from e
    if await blacklist.is_revoked(payload["jti"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен отозван, войдите снова", headers=_UNAUTHORIZED)
    return payload


def issued_before_revocation(user: User, payload: dict) -> bool:
    """Токен выпущен до смены пароля (users.tokens_valid_after) — недействителен."""
    after = user.tokens_valid_after
    if after is None:
        return False
    if after.tzinfo is None:  # SQLite отдаёт время без пояса
        after = after.replace(tzinfo=timezone.utc)
    issued_ms = payload.get("iat_ms", int(payload.get("iat", 0)) * 1000)
    return int(issued_ms) < int(after.timestamp() * 1000)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_current_user(
    payload: dict = Depends(get_current_token_payload),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await crud_user.get_user_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден", headers=_UNAUTHORIZED)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт деактивирован")
    if issued_before_revocation(user, payload):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_ENDED, headers=_UNAUTHORIZED)
    return user
