import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.credential import CredentialCreate


class StoreOut(BaseModel):
    id: uuid.UUID
    name: str
    is_main: bool  # первый магазин — сам аккаунт: его не отключить
    is_active: bool  # отключённый — не в переключателе, не синхронизируется, данные сохранены
    client_id_masked: str | None  # кабинет Ozon магазина (Client-Id частично скрыт)
    keys: int
    active_keys: int  # действующие (не отключённые и не истёкшие)
    unread_alerts: int  # уведомления без «Ок»
    created_at: datetime


class StoreCreate(CredentialCreate):
    """Новый магазин — сразу с ключом его кабинета Ozon: ключ проверяется в Ozon, данные начинают загружаться."""

    name: str | None = Field(default=None, max_length=100)


class StorePatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
