"""«Внешний трафик» (шаг 11.1): префикс UTM_CAMPAIGN, каналы, сборка ссылок с UTM-метками."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class MacroOut(BaseModel):
    code: str
    label: str


class ChannelOut(BaseModel):
    key: str
    label: str
    source: str
    medium: str
    fixed_source: bool  # рекламная сеть: utm_source задаётся сам
    hint: str
    macros: list[MacroOut]
    source_options: list[str]  # подсказки в выпадающем списке; можно вписать своё
    medium_options: list[str]


class TrafficSettings(BaseModel):
    utm_prefix: str | None  # vendor_org_<ID организации>; null — не задан
    cabinet_url: str  # раздел кабинета Ozon, где взять префикс
    channels: list[ChannelOut]
    sections: dict[str, str]  # раздел Ozon → название
    max_links: int


class PrefixUpdate(BaseModel):
    utm_prefix: str | None = Field(None, max_length=60)  # пусто или null — убрать


class BuildRequest(BaseModel):
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    urls: str = Field("", max_length=100_000)  # ссылки по одной в строке
    channel: str
    campaign: str = Field("", max_length=100)  # название кампании — после префикса
    source: str | None = None
    medium: str | None = None
    term: str | None = None
    content: str | None = None


class ProductRef(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None
    sku: int | None


class BuildRow(BaseModel):
    kind: Literal["product", "url"]
    line: int | None  # № строки в списке ссылок; у товара — нет
    input: str
    url: str | None
    section: str | None
    error: str | None
    product: ProductRef | None = None
    link_id: uuid.UUID | None = None  # сохранённая ссылка (шаг 11.2); у строки с ошибкой — нет


class BuildResult(BaseModel):
    campaign: str  # полное значение utm_campaign
    source: str
    medium: str
    term: str
    content: str
    rows: list[BuildRow]
    ok: int
    errors: int
    warnings: list[str]


class ProductOption(ProductRef):
    archived: bool


# ---------- история ссылок (шаг 11.2) ----------


class DeeplinkStatus(BaseModel):
    state: Literal["ok", "soon", "expired"]
    expires_on: date  # до какого дня работает без новых переходов
    days_left: int  # отрицательное — уже не работает
    last_activity: datetime  # последний известный переход или когда добавлен диплинк


class LinkRow(BaseModel):
    link_id: uuid.UUID
    version: int
    versions: int
    created_at: datetime  # когда создана (первая версия)
    updated_at: datetime  # когда изменена (эта версия)
    product: ProductRef | None
    source_url: str
    section: str
    channel: str
    campaign: str
    campaign_name: str
    utm_source: str
    utm_medium: str
    utm_term: str
    utm_content: str
    url: str
    deeplink: str | None
    deeplink_status: DeeplinkStatus | None
    substitute_code: str | None
    deleted: bool


class LinkCounts(BaseModel):
    all: int
    deeplink: int
    expiring: int  # истекает за 7 дней или уже не работает
    substitute: int
    deleted: int


class LinkFacets(BaseModel):
    campaigns: list[str]
    sources: list[str]
    mediums: list[str]


class LinkList(BaseModel):
    items: list[LinkRow]
    total: int
    counts: LinkCounts
    facets: LinkFacets


class LinkUpdate(BaseModel):
    """Не переданное поле не меняется; пустая строка у диплинка и подменного артикула — убрать."""

    campaign: str | None = Field(None, max_length=100)  # название кампании, после префикса
    source: str | None = Field(None, max_length=200)
    medium: str | None = Field(None, max_length=200)
    term: str | None = Field(None, max_length=200)
    content: str | None = Field(None, max_length=200)
    deeplink: str | None = Field(None, max_length=2000)
    substitute_code: str | None = Field(None, max_length=30)


class LinkSaved(BaseModel):
    link: LinkRow
    warnings: list[str]  # например: диплинк создан для прежних меток — убран


class LinkVersion(BaseModel):
    version: int
    created_at: datetime
    campaign: str
    utm_source: str
    utm_medium: str
    utm_term: str
    utm_content: str
    url: str
    deeplink: str | None
    substitute_code: str | None
    deleted_at: datetime | None
    superseded_at: datetime | None
