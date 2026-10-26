import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.models.api_credential import ApiCredential
from app.services.ozon_auth import mask_client_id

DEFAULT_LABEL = "Магазин Ozon"


def _aware(dt: datetime) -> datetime:
    # SQLite (тесты) отдаёт даты без часового пояса; храним всегда UTC
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CredentialCreate(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    client_id: str = Field(min_length=1, max_length=32)
    # SecretStr: значение не попадёт в repr/логи модели
    api_key: SecretStr = Field(min_length=1, max_length=200)
    # Обычно срок сообщает сам Ozon (/v1/roles); это поле — запасной вариант, иначе +3 месяца
    expires_at: datetime | None = None

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v else None

    @field_validator("client_id")
    @classmethod
    def digits_only(cls, v: str) -> str:
        v = v.strip()
        if not (v.isascii() and v.isdigit()):
            raise ValueError("только цифры")
        return v

    @field_validator("api_key")
    @classmethod
    def strip_key(cls, v: SecretStr) -> SecretStr:
        stripped = v.get_secret_value().strip()
        if not stripped:
            raise ValueError("обязательное поле")
        return SecretStr(stripped)

    @field_validator("expires_at")
    @classmethod
    def in_future(cls, v: datetime | None) -> datetime | None:
        if v is not None and _aware(v) <= datetime.now(timezone.utc):
            raise ValueError("дата должна быть в будущем")
        return _aware(v) if v is not None else None


class CredentialResponse(BaseModel):
    id: uuid.UUID
    label: str
    client_id_masked: str
    is_active: bool
    expires_at: datetime
    days_until_expiry: int
    last_used_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, c: ApiCredential) -> "CredentialResponse":
        expires_at = _aware(c.expires_at)
        left = expires_at - datetime.now(timezone.utc)
        return cls(
            id=c.id,
            label=c.label,
            client_id_masked=mask_client_id(c.client_id),
            is_active=c.is_active,
            expires_at=expires_at,
            days_until_expiry=left.days,  # floor: истёк → отрицательное
            last_used_at=_aware(c.last_used_at) if c.last_used_at else None,
            created_at=_aware(c.created_at),
        )


class VerifyResponse(BaseModel):
    ok: bool
    message: str
    credential: CredentialResponse


class CredentialReplace(BaseModel):
    """«Обновить доступ» (шаг 10.26): новый Api-Key того же кабинета (Client-Id не меняется) — вместо старого ключа."""

    api_key: SecretStr = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None
