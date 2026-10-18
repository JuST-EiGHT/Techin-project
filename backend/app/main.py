import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.router import api_router
from app.core.audit_middleware import AuditMiddleware
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.monitoring import init_sentry
from app.db.session import get_db
from app.services.encryption import init_encryption

HEALTH_TIMEOUT = 3  # секунды на проверку БД и Redis: зависший сервис — «error», а не зависший /health


def check_production_settings() -> None:
    """На сервере (ENVIRONMENT=production) с ключом по умолчанию или адресом без https API не стартует."""
    if settings.is_production and (problems := settings.production_problems()):
        raise RuntimeError("Настройки сервера (.env.prod) не готовы: " + "; ".join(problems))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_encryption()  # нет/невалиден ENCRYPTION_KEY → приложение не стартует с понятной ошибкой
    check_production_settings()
    yield


async def _check_db(db: AsyncSession) -> str:
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), HEALTH_TIMEOUT)
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


async def check_redis() -> str:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        try:
            await asyncio.wait_for(client.ping(), HEALTH_TIMEOUT)
        finally:
            await client.aclose()
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def create_app() -> FastAPI:
    # Документация API (/docs, /redoc, /openapi.json) — только при разработке: на сервере она подсказала бы чужому все
    # эндпоинты
    docs = not settings.is_production
    init_sentry("api")
    application = FastAPI(
        title="Ozon Analytics API", version=settings.APP_VERSION, lifespan=lifespan,
        docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(AuditMiddleware)  # изменяющие запросы → audit_log (шаг 6.1)
    register_error_handlers(application)
    application.include_router(api_router)

    @application.get("/health", tags=["system"])
    async def health(response: Response, db: AsyncSession = Depends(get_db)):
        """Жив ли сервис: БД и Redis отвечают. Для проверки после выкладки (scripts/deploy.sh), healthcheck Docker и
        внешнего мониторинга; 503 — что-то из них недоступно. version — коммит сборки."""
        checks = {"db": await _check_db(db), "redis": await check_redis()}
        ok = all(v == "ok" for v in checks.values())
        if not ok:
            response.status_code = 503
        return {"status": "ok" if ok else "error", "version": settings.APP_VERSION, **checks}

    return application


app = create_app()
