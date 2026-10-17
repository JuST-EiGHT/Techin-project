import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PriceProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class PricePreviewRequest(BaseModel):
    product_id: uuid.UUID
    price: Decimal = Field(gt=0, le=100_000_000, decimal_places=2)
    reason: str | None = Field(None, max_length=500)  # для истории изменений, необязательно


class PricePreview(BaseModel):
    """Что будет при новой цене. В Ozon ничего не меняется; применить — POST /prices/apply с preview_id."""

    preview_id: uuid.UUID
    created_at: datetime
    expires_at: datetime  # после — предпросмотр не применить, нужен новый
    can_apply: bool
    errors: list[str]  # блокируют
    warnings: list[str]
    product: PriceProduct
    price_before: Decimal | None  # сейчас в Ozon
    price_after: Decimal
    old_price: Decimal | None  # цена до скидки (зачёркнутая) — сервис её не меняет
    min_price_ozon: Decimal | None  # минимальная цена в Ozon (для автоакций) — не меняется
    change_pct: Decimal | None
    change_rub: Decimal | None
    floor: Decimal | None  # ниже нельзя: себестоимость с НДС × 1,05
    allowed_from: Decimal | None  # что можно поставить за один раз
    allowed_to: Decimal | None
    next_change_at: datetime | None  # пауза: раньше менять нельзя
    profit_before: Decimal | None  # останется с 1 шт. до налога на прибыль
    profit_after: Decimal | None
    margin_before: Decimal | None
    margin_after: Decimal | None
    min_price: Decimal | None  # наша минимальная (без убытка) и целевая — калькулятор 4.2
    target_price: Decimal | None
    margin_note: str | None  # почему маржу не посчитать
    price_checked_at: datetime | None  # когда узнали текущую цену
    price_fresh: bool  # только что из Ozon (иначе — по последнему снимку)
    reason: str | None


class PriceApplyRequest(BaseModel):
    preview_id: uuid.UUID


class PriceApplyResult(BaseModel):
    change_id: uuid.UUID
    status: str  # applied
    applied: bool
    product: PriceProduct
    price_before: Decimal | None
    price_after: Decimal
    warnings: list[str]
    message: str
