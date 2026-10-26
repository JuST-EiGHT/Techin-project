from datetime import datetime

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class TimezoneOption(BaseModel):
    value: str  # Europe/Moscow
    label: str  # Москва (UTC+3)


class SectionSettings(BaseModel):
    """Раздел уведомлений (10.25): что в нём и как уведомлять."""

    key: str  # sales | margin | stock | cash | payouts | auto_promo | prices | data | automation
    title: str
    hint: str
    severities: list[Literal["red", "yellow"]]  # какие бывают; одна — выбор важности не показываем
    soon: str | None  # раздела ещё нет — с какого шага (автоматизация — 9.x)
    on: bool  # уведомлять: колокольчик, «Требует внимания», Telegram
    telegram: bool
    severity: Literal["all", "red"]  # в Telegram: все или только красные
    daily: bool  # в Telegram сводкой раз в день


class Channel(BaseModel):
    key: Literal["service", "telegram", "email"]
    title: str
    available: bool  # можно включить
    connected: bool  # подключён (в сервисе — всегда)
    note: str  # что это и что сделать


class NotificationSettingsResponse(BaseModel):
    """Токен бота сюда не входит никогда — только имя бота для ссылки."""

    bot_configured: bool  # на сервере задан TELEGRAM_BOT_TOKEN
    bot_username: str | None  # имя бота без @ — ссылка t.me/<имя>?start=<код>
    linked: bool
    telegram_username: str | None
    telegram_name: str | None
    linked_at: datetime | None
    blocked: bool  # бот не может написать: остановили или заблокировали в Telegram
    sections: list[SectionSettings]  # по разделам карты (10.25)
    channels: list[Channel]  # в сервисе, Telegram, e-mail (после почты — 8.3a)
    digest_at: str  # «09:00» — сводка раз в день, местное время
    quiet_enabled: bool
    quiet_from: str  # «23:00» — местное время пользователя
    quiet_to: str  # «08:00»
    timezone: str
    timezones: list[TimezoneOption]
    pending: int  # ждут отправки (тихие часы, повтор)
    last_sent_at: datetime | None


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class SectionPatch(BaseModel):
    on: bool | None = None
    telegram: bool | None = None
    severity: Literal["all", "red"] | None = None
    daily: bool | None = None


class NotificationSettingsUpdate(BaseModel):
    """Не переданное поле не меняется; у раздела — тоже."""

    sections: dict[str, SectionPatch] | None = None
    digest_at: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_enabled: bool | None = None
    quiet_from: str | None = Field(None, pattern=HHMM)
    quiet_to: str | None = Field(None, pattern=HHMM)
    timezone: str | None = Field(None, max_length=64)


class LinkCode(BaseModel):
    code: str  # шесть цифр
    expires_at: datetime
    bot_username: str | None
    deep_link: str | None  # https://t.me/<бот>?start=<код> — бот получит «/start <код>» сам


class TestResult(BaseModel):
    sent_at: datetime
    message: str


class ThresholdOut(BaseModel):
    key: str  # поле настроек магазина: anomaly_sales_drop_pct …
    label: str
    unit: str
    why: str  # на что влияет (правило 15)
    value: Decimal
    default: Decimal
    min: Decimal
    max: Decimal
    places: int  # знаков после запятой: 0 — целое


class ThresholdSection(BaseModel):
    section: str
    note: str | None  # общее пояснение раздела: как считаем, что ещё влияет
    items: list[ThresholdOut]


class ThresholdsOut(BaseModel):
    """Пороги предупреждений текущего магазина (10.25; до этого — «Настройки → Аномалии и порог маржи», шаг 7.1)."""

    sections: list[ThresholdSection]
    defaults: bool  # ничего не меняли
    stock: dict[str, int]  # пороги остатков 5.1 (critical, low, excess) — меняются в «Настройках → Остатки»


class ThresholdsUpdate(BaseModel):
    """values — новые значения (не переданные не меняются); reset — все вернуть по умолчанию."""

    values: dict[str, Decimal] = {}
    reset: bool = False
