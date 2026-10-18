from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCTION = "production"
DEFAULT_SECRET = "change_me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"  # production — на сервере: закрыты /docs и /openapi.json, проверяются секреты
    DEBUG: bool = False
    # Версия сборки (коммит) — в /health и в Sentry; задаёт scripts/deploy.sh при сборке образа
    APP_VERSION: str = "dev"

    DATABASE_URL: str = "postgresql+asyncpg://ozon:ozon_secret@db:5432/ozon_analytics"
    REDIS_URL: str = "redis://redis:6379/0"

    SECRET_KEY: str = DEFAULT_SECRET
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

    # Кто может зарегистрироваться (шаг 8.4): closed — никто, аккаунты создаёт администратор командой
    # (`python -m app.admin create-user`); invite — только по коду приглашения (`python -m app.admin invite`);
    # open — любой. Не задано: на сервере — closed (почты ещё нет, шаг 8.3a), при разработке — open.
    REGISTRATION_MODE: Literal["closed", "invite", "open"] | None = None
    INVITE_DAYS: int = 7  # сколько дней действует приглашение

    # Лимит неудачных попыток входа (шаг 8.4): с одного IP на один email и с одного IP на любые email за окно —
    # дальше 429 до конца окна. Неверные коды приглашения — тот же счётчик IP, окно — час.
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_MAX_ATTEMPTS_PER_IP: int = 20
    LOGIN_WINDOW_MINUTES: int = 15
    INVITE_MAX_ATTEMPTS_PER_IP: int = 10

    # Ошибки — в Sentry или совместимый сервис (GlitchTip); пусто — не отправляются
    SENTRY_DSN: str = ""

    # В .env задаётся строкой через запятую
    ALLOWED_ORIGINS: list[str] | str = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == PRODUCTION

    @property
    def registration_mode(self) -> str:
        return self.REGISTRATION_MODE or ("closed" if self.is_production else "open")

    def production_problems(self) -> list[str]:
        """Что не так с настройками для сервера: с этим API в проде не стартует."""
        problems = []
        if self.SECRET_KEY == DEFAULT_SECRET or len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY: задайте случайную строку от 32 символов (openssl rand -hex 32)")
        if self.DEBUG:
            problems.append("DEBUG: на сервере должно быть false")
        if not self.SITE_URL.startswith("https://"):
            problems.append("SITE_URL: адрес сайта на сервере — https://ваш-домен")
        if any(o.startswith("http://") for o in self.ALLOWED_ORIGINS):
            problems.append("ALLOWED_ORIGINS: на сервере — только https://ваш-домен")
        return problems


settings = Settings()
