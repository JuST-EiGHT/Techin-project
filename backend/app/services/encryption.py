"""Шифрование секретов (API-ключей Ozon) через Fernet.

Ключ берётся из ENCRYPTION_KEY и проверяется при старте приложения (init_encryption
в lifespan, см. main.py) — чтобы неверная настройка всплывала сразу, а не на первом
запросе пользователя.
"""
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_HOW_TO = 'Сгенерируйте ключ: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'


class EncryptionKeyError(RuntimeError):
    """ENCRYPTION_KEY не задан или невалиден."""


class DecryptionError(Exception):
    """Шифротекст не расшифровывается текущим ключом."""


_fernet: Fernet | None = None


def _build(key: str) -> Fernet:
    if not key or key == "change_me":
        raise EncryptionKeyError(f"ENCRYPTION_KEY не задан в .env. {_HOW_TO}")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as e:
        raise EncryptionKeyError(f"ENCRYPTION_KEY невалиден: нужен 32-байтный ключ в url-safe base64. {_HOW_TO}") from e


def init_encryption(key: str | None = None) -> None:
    """Проверяет ключ и готовит шифратор. Бросает EncryptionKeyError."""
    global _fernet
    _fernet = _build(settings.ENCRYPTION_KEY if key is None else key)


def _get() -> Fernet:
    if _fernet is None:
        init_encryption()
    return _fernet  # type: ignore[return-value]


def encrypt(plain: str) -> str:
    return _get().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _get().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise DecryptionError("Не удалось расшифровать ключ — возможно, сменился ENCRYPTION_KEY") from e
