import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.core.config import settings
from app.integrations.ozon import (
    OzonAPIError,
    OzonAuthError,
    OzonClient,
    OzonNetworkError,
    OzonNotConfiguredError,
    OzonRateLimitError,
    OzonServerError,
    OzonTimeoutError,
    get_ozon_client,
)
from app.integrations.ozon.rate_limiter import MemoryWindow, OzonRateLimiter, RedisWindow
from app.models.api_credential import ApiCredential
from app.models.user import User
from app.services.encryption import encrypt

CLIENT_ID = "1234567"
API_KEY = "a1b2c3d4-0000-4bad-beef-secretOZONkey"
PATH = "/v3/product/list"
OK_BODY = {"result": {"items": [{"product_id": 1, "offer_id": "6001-2RS"}], "total_items": 1, "last_id": ""}}


class Sleeps(list):
    """Подмена asyncio.sleep: запоминает паузы и не ждёт."""

    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


@pytest.fixture
def sleeps():
    return Sleeps()


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield mock


def make_client(sleeps, **kw) -> OzonClient:
    limiter = OzonRateLimiter(CLIENT_ID, backend=MemoryWindow(), sleep=sleeps)
    return OzonClient(CLIENT_ID, API_KEY, sleep=sleeps, rate_limiter=limiter, **kw)


# ── клиент: успех, повторы, ошибки ──
async def test_success_sends_headers_and_json(ozon, sleeps):
    route = ozon.post(PATH).respond(200, json=OK_BODY)
    async with make_client(sleeps) as c:
        data = await c.request("POST", PATH, {"filter": {}, "last_id": "", "limit": 1})
    assert data == OK_BODY and sleeps == []
    req = route.calls.last.request
    assert req.headers["Client-Id"] == CLIENT_ID and req.headers["Api-Key"] == API_KEY
    assert req.url == f"{settings.OZON_API_URL}{PATH}"


def test_defaults_match_step_spec():
    from app.core.config import Settings

    assert Settings.model_fields["OZON_API_URL"].default == "https://api-seller.ozon.ru"
    c = OzonClient(CLIENT_ID, API_KEY)
    assert str(c._http.base_url).rstrip("/") == "https://ozon.test"  # в тестах URL подменён через env
    assert c._http.timeout.read == 30.0
    assert API_KEY not in repr(c)


async def test_network_error_retried_with_backoff(ozon, sleeps):
    ozon.post(PATH).mock(side_effect=[httpx.ConnectError("refused"), httpx.ConnectError("refused"), httpx.Response(200, json=OK_BODY)])
    async with make_client(sleeps) as c:
        assert await c.request("POST", PATH, {}) == OK_BODY
    assert sleeps == [1, 2]


async def test_network_error_exhausted(ozon, sleeps):
    route = ozon.post(PATH).mock(side_effect=httpx.ConnectError("refused"))
    async with make_client(sleeps) as c:
        with pytest.raises(OzonNetworkError) as e:
            await c.request("POST", PATH, {})
    assert route.call_count == 4 and sleeps == [1, 2, 4]  # 1 попытка + 3 повтора
    assert "Нет связи с Ozon" in str(e.value)


async def test_timeout(ozon, sleeps):
    route = ozon.post(PATH).mock(side_effect=httpx.ReadTimeout("timed out"))
    async with make_client(sleeps) as c:
        with pytest.raises(OzonTimeoutError) as e:
            await c.request("POST", PATH, {})
    assert route.call_count == 4
    assert str(e.value) == "Ozon не ответил вовремя, попробуйте позже"
    assert e.value.status_code is None and e.value.path == PATH


async def test_429_uses_retry_after(ozon, sleeps):
    ozon.post(PATH).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json=OK_BODY)])
    async with make_client(sleeps) as c:
        assert await c.request("POST", PATH, {}) == OK_BODY
    assert sleeps == [3]


async def test_429_exponential_then_error(ozon, sleeps):
    route = ozon.post(PATH).respond(429, json={"code": 8, "message": "rate limit exceeded"})
    async with make_client(sleeps) as c:
        with pytest.raises(OzonRateLimitError) as e:
            await c.request("POST", PATH, {})
    assert sleeps == [1, 2, 4, 8, 16, 30] and route.call_count == 7
    assert e.value.status_code == 429 and "ограничил частоту" in str(e.value)


async def test_5xx_retried_twice_after_5s(ozon, sleeps):
    ozon.post(PATH).mock(side_effect=[httpx.Response(502), httpx.Response(500), httpx.Response(200, json=OK_BODY)])
    async with make_client(sleeps) as c:
        assert await c.request("POST", PATH, {}) == OK_BODY
    assert sleeps == [5, 5]


async def test_5xx_exhausted(ozon, sleeps):
    route = ozon.post(PATH).respond(503, text="Service Unavailable")
    async with make_client(sleeps) as c:
        with pytest.raises(OzonServerError) as e:
            await c.request("POST", PATH, {})
    assert route.call_count == 3 and e.value.status_code == 503


@pytest.mark.parametrize("status,text", [(401, "неверный Client-Id или Api-Key"), (403, "нет прав")])
async def test_auth_errors_not_retried(ozon, sleeps, status, text):
    route = ozon.post(PATH).respond(status, json={"code": 16, "message": "Invalid Api-Key, please contact support"})
    async with make_client(sleeps) as c:
        with pytest.raises(OzonAuthError) as e:
            await c.request("POST", PATH, {})
    assert route.call_count == 1 and sleeps == []
    assert e.value.status_code == status and text in str(e.value)
    assert e.value.ozon_message == "Invalid Api-Key, please contact support"


async def test_other_4xx_is_api_error_without_retry(ozon, sleeps):
    route = ozon.post(PATH).respond(400, json={"code": 3, "message": "limit must be <= 1000"})
    async with make_client(sleeps) as c:
        with pytest.raises(OzonAPIError) as e:
            await c.request("POST", PATH, {"limit": 5000})
    assert type(e.value) is OzonAPIError and route.call_count == 1
    assert str(e.value) == "Ozon отклонил запрос (HTTP 400)" and e.value.ozon_message == "limit must be <= 1000"


async def test_no_retry_mode(ozon, sleeps):
    route = ozon.post(PATH).respond(500)
    async with make_client(sleeps, retry=False) as c:
        with pytest.raises(OzonServerError):
            await c.request("POST", PATH, {})
    assert route.call_count == 1 and sleeps == []


async def test_non_json_success(ozon, sleeps):
    ozon.post(PATH).respond(200, text="<html>")
    async with make_client(sleeps) as c:
        with pytest.raises(OzonAPIError, match="неожиданном формате"):
            await c.request("POST", PATH, {})


# ── логирование ──
async def test_log_has_method_path_status_time(ozon, sleeps, caplog):
    caplog.set_level(logging.INFO, logger="app.integrations.ozon.client")
    ozon.post(PATH).respond(200, json=OK_BODY)
    async with make_client(sleeps) as c:
        await c.request("POST", PATH, {})
    msg = next(r.getMessage() for r in caplog.records if r.name == "app.integrations.ozon.client")
    assert msg.startswith(f"Ozon POST {PATH} → 200 за ") and " мс (client ••••4567)" in msg


async def test_api_key_never_in_logs_or_errors(ozon, sleeps, caplog):
    caplog.set_level(logging.DEBUG)  # все логгеры, включая httpx/httpcore
    errors: list[OzonAPIError] = []
    scenarios = [
        [httpx.Response(200, json=OK_BODY)],
        [httpx.ConnectError(f"refused {API_KEY}")] * 4,  # даже если ключ окажется в тексте исключения httpx
        [httpx.ReadTimeout(f"timeout {API_KEY}")] * 4,
        [httpx.Response(401, json={"message": f"Invalid Api-Key {API_KEY}"})],  # и в ответе Ozon
        [httpx.Response(403, text=f"denied for {API_KEY}")],
        [httpx.Response(429, json={"message": API_KEY})] * 7,
        [httpx.Response(500, json={"message": API_KEY})] * 3,
        [httpx.Response(400, json={"message": f"bad {API_KEY}"})],
    ]
    for side_effect in scenarios:
        ozon.post(PATH).mock(side_effect=side_effect)
        async with make_client(sleeps) as c:
            try:
                await c.request("POST", PATH, {"limit": 1}, items=1)
            except OzonAPIError as e:
                errors.append(e)

    assert len(errors) == len(scenarios) - 1
    assert API_KEY not in caplog.text
    assert caplog.records, "логи должны были писаться — иначе тест ничего не проверяет"
    for e in errors:
        assert API_KEY not in str(e) and API_KEY not in repr(e) and API_KEY not in e.ozon_message
        assert e.__cause__ is None or API_KEY not in str(e.__cause__)


# ── rate limiter ──
class FakeTime:
    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


async def _redis_backend():
    import redis.asyncio as aioredis

    return RedisWindow(aioredis.from_url(os.environ["TEST_REDIS_URL"]))


BACKENDS = ["memory"] + (["redis"] if os.environ.get("TEST_REDIS_URL") else [])


@pytest.fixture(params=BACKENDS)
async def limiter_factory(request):
    made = []

    async def make(limit=100, client_id=None):
        backend = MemoryWindow() if request.param == "memory" else await _redis_backend()
        t = FakeTime()
        lim = OzonRateLimiter(client_id or f"test-{uuid.uuid4().hex}", limit=limit, backend=backend, clock=t.clock, sleep=t.sleep)
        made.append(lim)
        return lim, t

    yield make
    for lim in made:
        await lim.aclose()


async def test_limiter_within_limit_no_wait(limiter_factory):
    lim, t = await limiter_factory(limit=100)
    await lim.check_and_acquire(60)
    await lim.check_and_acquire(40)
    assert t.sleeps == []


async def test_limiter_waits_whole_batch(limiter_factory):
    # 70 занято, просим 50 при лимите 100: частично не отправляем — ждём, пока освободятся 70
    lim, t = await limiter_factory(limit=100)
    await lim.check_and_acquire(70)
    t.now += 10
    await lim.check_and_acquire(50)
    assert len(t.sleeps) == 1 and t.sleeps[0] == pytest.approx(50.01)


async def test_limiter_frees_oldest_first(limiter_factory):
    lim, t = await limiter_factory(limit=100)
    await lim.check_and_acquire(30)  # t=0
    t.now += 20
    await lim.check_and_acquire(30)  # t=20
    t.now += 20
    await lim.check_and_acquire(40)  # t=40, занято 100
    await lim.check_and_acquire(25)  # хватит освобождения первой записи (t=0 → в t=60)
    assert t.sleeps[0] == pytest.approx(20.01)


async def test_limiter_rejects_batch_over_limit(limiter_factory):
    lim, _ = await limiter_factory(limit=100)
    with pytest.raises(ValueError, match="больше лимита"):
        await lim.check_and_acquire(101)
    await lim.check_and_acquire(0)  # 0 товаров — ничего не резервируем


async def test_limiter_counts_per_client(limiter_factory):
    a, ta = await limiter_factory(limit=100, client_id=f"a-{uuid.uuid4().hex}")
    b, tb = await limiter_factory(limit=100, client_id=f"b-{uuid.uuid4().hex}")
    await a.check_and_acquire(100)
    await b.check_and_acquire(100)
    assert ta.sleeps == [] and tb.sleeps == []


async def test_client_reserves_items_on_every_attempt(ozon):
    t = FakeTime()
    backend = MemoryWindow()
    limiter = OzonRateLimiter(CLIENT_ID, limit=1000, backend=backend, clock=t.clock, sleep=t.sleep)
    ozon.post(PATH).mock(side_effect=[httpx.Response(500), httpx.Response(200, json=OK_BODY)])
    async with OzonClient(CLIENT_ID, API_KEY, rate_limiter=limiter, sleep=t.sleep) as c:
        await c.request("POST", PATH, {}, items=400)
    # 2 попытки × 400; следующий запрос на 400 уже не помещается в окно
    assert await backend.try_acquire(limiter.key, 400, t.now, 60, 1000) > 0


# ── фабрика ──
@pytest.fixture
async def db(client):
    from app.db.session import get_db
    from app.main import app

    gen = app.dependency_overrides[get_db]()
    session = await gen.__anext__()
    yield session
    await gen.aclose()


async def _user(db, email="seller@example.com") -> User:
    u = User(email=email, hashed_password="x")
    db.add(u)
    await db.commit()
    return u


def _cred(user, **kw) -> ApiCredential:
    now = datetime.now(timezone.utc)
    data = dict(
        user_id=user.id, label="k", client_id=CLIENT_ID, api_key_encrypted=encrypt(API_KEY),
        is_active=True, expires_at=now + timedelta(days=100), last_used_at=None,
    )
    return ApiCredential(**{**data, **kw})


async def test_factory_no_key(db):
    user = await _user(db)
    with pytest.raises(OzonNotConfiguredError, match="API-ключ"):
        await get_ozon_client(user.id, db)


async def test_factory_skips_inactive_expired_and_foreign(db):
    user, other = await _user(db), await _user(db, "other@example.com")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    db.add_all([_cred(user, is_active=False), _cred(user, expires_at=past), _cred(other)])
    await db.commit()
    with pytest.raises(OzonNotConfiguredError):
        await get_ozon_client(user.id, db)


async def test_factory_returns_client_and_marks_used(db, ozon):
    user = await _user(db)
    stale = datetime.now(timezone.utc) - timedelta(days=5)
    db.add_all([
        _cred(user, client_id="1111111", last_used_at=stale - timedelta(days=1)),
        _cred(user, client_id="2222222", last_used_at=stale),
    ])
    await db.commit()

    client = await get_ozon_client(user.id, db)
    try:
        assert client.client_id == "2222222"  # последний использованный
        route = ozon.post(PATH).respond(200, json=OK_BODY)
        await client.request("POST", PATH, {})
        assert route.calls.last.request.headers["Api-Key"] == API_KEY  # ключ расшифрован
    finally:
        await client.aclose()

    used = (await db.get(ApiCredential, next(c.id for c in await _all(db) if c.client_id == "2222222"))).last_used_at
    assert used.replace(tzinfo=used.tzinfo or timezone.utc) > stale + timedelta(days=4)


async def _all(db):
    from sqlalchemy import select

    return list((await db.execute(select(ApiCredential))).scalars())


async def test_factory_undecryptable_key(db):
    user = await _user(db)
    db.add(_cred(user, api_key_encrypted="not-a-fernet-token"))
    await db.commit()
    with pytest.raises(OzonNotConfiguredError, match="не расшифровывается"):
        await get_ozon_client(user.id, db)
