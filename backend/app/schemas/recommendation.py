import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class RecommendationProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class RecommendationItem(BaseModel):
    kind: str  # promo_loss | price_below_min | supply | missing_cost | withdraw
    key: str  # для «Скрыть»
    title: str  # что сделать
    why: str  # почему — условие и цифры
    basis: str  # на каком расчёте основано
    action: str  # подпись кнопки
    link: str
    product: RecommendationProduct | None
    money: Decimal | None  # сколько на кону, ₽ — где можно оценить
    money_label: str | None  # «убыток за 30 дней», «заморожено в товаре» …


class RecommendationsPage(BaseModel):
    items: list[RecommendationItem]
    total: int
    hidden: int  # скрыто сейчас
    data_days: int  # дней с данными о продажах за 30
    min_days: int  # нужно не меньше
    enough_data: bool
    hide_days: int  # «Скрыть» — на сколько дней


class HideIn(BaseModel):
    key: str = Field(min_length=1, max_length=120)


class HideOut(BaseModel):
    hidden_until: datetime
    hide_days: int


class UnhideOut(BaseModel):
    restored: int
