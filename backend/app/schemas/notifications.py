from datetime import datetime

from pydantic import BaseModel, Field


class TimezoneOption(BaseModel):
    value: str  # Europe/Moscow
    label: str  # Москва (UTC+3)


class NotificationSettingsResponse(BaseModel):
    """Токен бота сюда не входит никогда — только имя бота для ссылки."""

    bot_configured: bool  # на сервере задан TELEGRAM_BOT_TOKEN
    bot_username: str | None  # имя бота без @ — ссылка t.me/<имя>?start=<код>
    linked: bool
    telegram_username: str | None
    telegram_name: str | None
    linked_at: datetime | None
    blocked: bool  # бот не может написать: остановили или заблокировали в Telegram
    notify_red: bool  # красные (кроме денег и автоакций)
    notify_yellow: bool  # жёлтые
    notify_auto_promo: bool  # Ozon сам добавил товар в акцию
    notify_money: bool  # выплаты Ozon, сверка с банком, кассовый разрыв, операции без статьи
    quiet_enabled: bool
    quiet_from: str  # «23:00» — местное время пользователя
    quiet_to: str  # «08:00»
    timezone: str
    timezones: list[TimezoneOption]
    pending: int  # ждут отправки (тихие часы, повтор)
    last_sent_at: datetime | None


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class NotificationSettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    notify_red: bool | None = None
    notify_yellow: bool | None = None
    notify_auto_promo: bool | None = None
    notify_money: bool | None = None
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
