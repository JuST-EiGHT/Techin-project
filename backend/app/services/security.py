"""Хеширование паролей и JWT. Без обращения к БД — чтобы избежать циклических импортов."""
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS = "access"
REFRESH = "refresh"


class InvalidTokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _pwd.verify(password, hashed)
    except ValueError:
        return False


def _create_token(user_id: uuid.UUID, token_type: str, lifetime: timedelta, sid: uuid.UUID | None = None) -> tuple[str, str, int]:
    now = datetime.now(timezone.utc)
    exp = now + lifetime
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id), "type": token_type, "jti": jti, "iat": int(now.timestamp()), "exp": int(exp.timestamp()),
        "iat_ms": int(now.timestamp() * 1000),  # точнее iat: отсечь токены, выпущенные до смены пароля
    }
    if sid is not None:
        payload["sid"] = str(sid)  # сеанс входа (шаг 10.26): завершённый — токен не принимается
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM), jti, int(exp.timestamp())


def create_access_token(user_id: uuid.UUID, sid: uuid.UUID | None = None) -> str:
    return _create_token(user_id, ACCESS, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), sid)[0]


def create_refresh_token(user_id: uuid.UUID, sid: uuid.UUID | None = None) -> str:
    return _create_token(user_id, REFRESH, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS), sid)[0]


def decode_token(token: str, expected_type: str | None = None) -> dict:
    """Возвращает payload или бросает InvalidTokenError."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as e:
        raise InvalidTokenError("Токен недействителен или истёк") from e
    if not all(k in payload for k in ("sub", "type", "jti", "exp")):
        raise InvalidTokenError("Некорректная структура токена")
    if expected_type and payload["type"] != expected_type:
        raise InvalidTokenError(f"Ожидался {expected_type}-токен")
    return payload
