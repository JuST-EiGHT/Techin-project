"""«Внешний трафик» (шаг 11.1): префикс UTM_CAMPAIGN, каналы, сборка ссылок с UTM-метками."""
import uuid
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
