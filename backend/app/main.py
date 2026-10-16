from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.audit_middleware import AuditMiddleware
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.services.encryption import init_encryption


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_encryption()  # нет/невалиден ENCRYPTION_KEY → приложение не стартует с понятной ошибкой
    yield


app = FastAPI(title="Ozon Analytics API", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)  # изменяющие запросы → audit_log (шаг 6.1)
register_error_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}
