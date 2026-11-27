"""Реферальная программа (шаг 11.7): настройки, участники, отметка «отправлено»."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.traffic import ProductRef

Channel = Literal["ozon_chat", "outside", "review", "other"]
Target = Literal["product", "store"]


class ProgramOut(BaseModel):
    campaign_name: str
    utm_source: str
    utm_medium: str
    store_url: str | None
    defaults: bool  # настройки ещё не меняли


class ReferralSettings(BaseModel):
    program: ProgramOut
    utm_prefix: str | None  # префикс магазина (11.1); нет — ссылки не собрать
    campaign: str | None  # utm_campaign программы целиком; нет префикса — null
    channels: dict[str, str]  # где отправили: ключ → название


class ProgramUpdate(BaseModel):
    campaign_name: str = Field(..., max_length=100)
    utm_source: str = Field(..., max_length=200)
    utm_medium: str = Field(..., max_length=200)
    store_url: str | None = Field(None, max_length=2000)


class ParticipantCreate(BaseModel):
    name: str = Field(..., max_length=300)  # как подписан отзыв
    product_id: uuid.UUID | None = None
    review_date: date | None = None
    note: str | None = Field(None, max_length=2000)
    target: Target = "product"  # куда ведёт ссылка: товар из отзыва или магазин


class ParticipantUpdate(BaseModel):
    """Не переданное поле не меняется; пустая заметка — убрать."""

    name: str | None = Field(None, max_length=300)
    review_date: date | None = None
    note: str | None = Field(None, max_length=2000)


class SentIn(BaseModel):
    channel: Channel
    sent_on: date | None = None  # нет — сегодня
    note: str | None = Field(None, max_length=2000)


class Sent(BaseModel):
    channel: str
    channel_title: str
    sent_on: date
    note: str | None
    marked_at: datetime


class Participant(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    product: ProductRef | None
    review_date: date | None
    note: str | None
    target: Target
    link_id: uuid.UUID
    url: str  # личная ссылка с метками
    status: Literal["new", "sent"]
    sent: Sent | None
    created_at: datetime
    deleted: bool


class ParticipantCounts(BaseModel):
    all: int
    new: int
    sent: int
    deleted: int


class ParticipantList(BaseModel):
    items: list[Participant]
    total: int
    counts: ParticipantCounts


class ParticipantEvent(BaseModel):
    kind: str
    title: str
    channel: str | None
    sent_on: date | None
    note: str | None
    created_at: datetime
