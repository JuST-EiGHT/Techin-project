import uuid
from datetime import date, datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PriceProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class PricePreviewRequest(BaseModel):
    product_id: uuid.UUID
    price: Decimal = Field(gt=0, le=100_000_000, decimal_places=2)
    reason: str | None = Field(None, max_length=500)  # для истории изменений, необязательно


class RollbackRef(BaseModel):
    """Какое изменение откатываем (шаг 6.4)."""

    id: uuid.UUID
    created_at: datetime
    price_before: Decimal | None  # к ней и вернём
    price_after: Decimal | None


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
    rollback_of: RollbackRef | None = None  # предпросмотр отката


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


# ── массовое изменение (шаг 6.3) ──
class BulkStrategy(BaseModel):
    """target_margin — цена с маржой value % (калькулятор 4.2); percent — текущая цена + value % (можно минус);
    min_plus — минимальная цена (без убытка) + value %."""

    kind: Literal["target_margin", "percent", "min_plus"]
    value: Decimal = Field(ge=-90, le=500, decimal_places=2)
    limit_step: bool = True  # дальше 20 % за раз — ближайшая допустимая цена

    @model_validator(mode="after")
    def _range(self):
        if self.kind == "target_margin" and not 0 <= self.value <= 90:
            raise ValueError("целевая маржа — от 0 до 90 %")
        if self.kind == "min_plus" and self.value < 0:
            raise ValueError("наценка к минимальной цене — не меньше 0 %")
        return self


class BulkPriceItem(BaseModel):
    product_id: uuid.UUID
    price: Decimal = Field(gt=0, le=100_000_000, decimal_places=2)


class BulkPreviewRequest(BaseModel):
    """Стратегия и product_ids — или готовые цены items."""

    strategy: BulkStrategy | None = None
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    items: list[BulkPriceItem] = Field(default_factory=list, max_length=1000)
    reason: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _one_way(self):
        if (self.strategy is None) == (not self.items):
            raise ValueError("укажите стратегию и товары или список цен")
        if self.strategy is not None and not self.product_ids:
            raise ValueError("выберите товары")
        return self


class BulkPreviewItem(BaseModel):
    preview_id: uuid.UUID
    product: PriceProduct
    group: str  # ok — применится, warn — с предупреждением, blocked — заблокировано
    price_before: Decimal | None
    price_after: Decimal | None  # None — цену не посчитать (причина в errors)
    change_pct: Decimal | None
    note: str | None  # пояснение к цене: «по стратегии — …, за один раз можно до …»
    line: int | None  # строка файла
    errors: list[str]
    warnings: list[str]
    profit_before: Decimal | None
    profit_after: Decimal | None
    margin_before: Decimal | None
    margin_after: Decimal | None


class BulkFileError(BaseModel):
    line: int
    offer_id: str
    message: str


class BulkFileInfo(BaseModel):
    rows: int  # строк с данными
    unchanged: int  # цена та же или пусто — не меняется
    errors: list[BulkFileError]


class BulkPreview(BaseModel):
    batch_id: uuid.UUID | None  # None — менять нечего
    created_at: datetime
    expires_at: datetime
    description: str | None  # стратегия, «Цены из файла» или комментарий — уйдёт в историю
    strategy: str | None
    price_fresh: bool
    warnings: list[str]
    counts: dict[str, int]  # ok, warn, blocked, unchanged, total
    items: list[BulkPreviewItem]
    file: BulkFileInfo | None


class BulkApplyRequest(BaseModel):
    batch_id: uuid.UUID
    include_warnings: bool = False  # применять и товары с предупреждениями
    confirmed: bool = False  # больше 10 товаров
    confirm_word: str | None = Field(None, max_length=50)  # больше 50 — «ПОДТВЕРЖДАЮ»


class BulkStatusItem(BaseModel):
    preview_id: uuid.UUID
    product: PriceProduct
    status: str  # applied | rejected | failed | unknown | blocked | queued | sending | not_applied
    price_before: Decimal | None
    price_after: Decimal | None
    batch_no: int | None
    error: str | None


class BulkBatch(BaseModel):
    no: int
    size: int
    state: str  # waiting | sending | done
    applied: int
    rejected: int
    failed: int
    unknown: int


class BulkStatus(BaseModel):
    batch_id: uuid.UUID
    started: bool
    finished: bool
    total: int
    counts: dict[str, int]
    batches: list[BulkBatch]
    items: list[BulkStatusItem]
    message: str | None


# ── история изменений (шаг 6.4) ──
class HistoryRef(BaseModel):
    id: uuid.UUID
    created_at: datetime
    status: str | None = None


class PriceHistoryItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    product: PriceProduct
    kind: str  # single — вручную, bulk — списком, rollback — откат, auto — правило
    kind_title: str
    reason: str | None
    price_before: Decimal | None
    price_after: Decimal | None
    change_pct: Decimal | None
    status: str  # applied | blocked | rejected | failed | unknown | queued
    status_title: str
    error: str | None  # почему не применилось
    warnings: list[str]
    batch_id: uuid.UUID | None  # массовое изменение
    rollback_of: HistoryRef | None  # это откат такого изменения
    rolled_back_by: HistoryRef | None  # это изменение откатили
    can_rollback: bool
    rollback_block: str | None  # почему нельзя откатить


class PriceHistoryPage(BaseModel):
    date_from: date
    date_to: date
    total: int
    limit: int
    offset: int
    counts: dict[str, int]  # по итогу за период (без фильтра итога): applied, blocked, …, not_applied, all
    items: list[PriceHistoryItem]
