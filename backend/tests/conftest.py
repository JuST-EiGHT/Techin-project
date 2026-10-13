import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"  # недоступен → in-memory blacklist
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ENCRYPTION_KEY"] = "q0bQ3jZ8m0kVq1z5c3k1m4yJgk1aB8tXz9tq2v6l1hE="  # тестовый Fernet-ключ
os.environ["OZON_API_URL"] = "https://ozon.test"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.token_blacklist import blacklist  # noqa: E402


@pytest.fixture
async def sessions():
    """Фабрика сессий тестовой БД (SQLite в памяти) — та же, что видит API в фикстуре client."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(sessions):
    async def _get_db():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    blacklist._memory.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def no_celery(monkeypatch):
    """Постановка задач в Celery — заглушка (брокера в тестах нет). Тесты, которым важно,
    что поставлено в очередь, подменяют enqueue_sync сами и читают этот список."""
    from app.api.v1 import sync as sync_api

    queued: list[tuple] = []
    monkeypatch.setattr(sync_api, "enqueue_sync", lambda *args: queued.append(args))
    return queued


USER = {"email": "Seller@Example.com", "password": "strongpass1", "full_name": "Радимир"}


@pytest.fixture
async def tokens(client):
    await client.post("/api/v1/auth/register", json=USER)
    r = await client.post("/api/v1/auth/login", json={"email": USER["email"], "password": USER["password"]})
    return r.json()
