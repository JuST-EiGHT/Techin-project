from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://ozon:ozon_secret@db:5432/ozon_analytics"
    REDIS_URL: str = "redis://redis:6379/0"

    SECRET_KEY: str = "change_me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24        # 24 часа
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Fernet-ключ для шифрования API-ключей Ozon. Без валидного ключа приложение не стартует.
    ENCRYPTION_KEY: str = ""

    OZON_API_URL: str = "https://api-seller.ozon.ru"
    OZON_TIMEOUT_SECONDS: float = 30.0
    OZON_RATE_LIMIT_PER_SECOND: int = 50  # запросов на все методы с одного Client ID (docs/OZON_API.md)

    # Банки по API (шаг 3.12): адреса — из документации банков (docs/banks/); песочницы — в .env.example
    TBANK_API_URL: str = "https://business.tbank.ru/openapi"
    TOCHKA_API_URL: str = "https://enter.tochka.com/uapi"

    REPORT_MAX_SIZE_MB: int = 10  # предел размера загружаемого отчёта (промпт 2.2)

    # Telegram-бот (шаг 7.3). Токен — только здесь (.env): не логируется и не возвращается в API. Пусто — уведомления в
    # Telegram выключены, на сайте — колокольчик. Имя бота (без @) — для ссылки «Открыть бота» с кодом привязки.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = ""
    # Свой сервер Bot API (https://github.com/tdlib/telegram-bot-api) или фейк для проверки; пусто — api.telegram.org
    TELEGRAM_API_URL: str = ""
    # Адрес сайта для ссылок в уведомлениях («Открыть в Ozon Analytics»)
    SITE_URL: str = "http://localhost:3000"

    # В .env задаётся строкой через запятую
    ALLOWED_ORIGINS: list[str] | str = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()
