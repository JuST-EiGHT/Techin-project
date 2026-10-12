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
    OZON_TIMEOUT_SECONDS: float = 15.0

    # В .env задаётся строкой через запятую
    ALLOWED_ORIGINS: list[str] | str = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()
