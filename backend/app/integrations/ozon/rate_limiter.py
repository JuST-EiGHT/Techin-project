"""Ограничение частоты запросов к Ozon Seller API.

По документации Ozon (раздел «Лимиты»): не больше 50 запросов в секунду на все
методы с одного Client ID; у части методов есть свой, более строгий лимит — он
указан в описании метода (METHOD_LIMITS). При превышении Ozon отвечает 429 с
заголовком Retry-After — это обрабатывает клиент.

Счётчик — скользящее окно в Redis: каждый запрос — запись в sorted set
`ozon:rl:<client_id>[:<метод>]`. Проверка и запись атомарны (Lua-скрипт), поэтому
API и все воркеры Celery вместе не превысят лимит одного кабинета.
Если Redis недоступен (тесты, локальный запуск) — тот же алгоритм в памяти процесса.
"""
import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.core.config import settings

log = logging.getLogger(__name__)

GLOBAL_WINDOW_SECONDS = 1.0

# Собственные лимиты методов из документации Ozon: путь → (запросов, окно в секундах)
METHOD_LIMITS: dict[str, tuple[int, float]] = {
    "/v2/products/stocks": (80, 60.0),  # «до 80 запросов в минуту» с аккаунта продавца
    "/v1/product/placement-zone/info": (10, 1.0),  # «не больше 10 запросов в секунду»
}

_KEY_PREFIX = "ozon:rl:"
_REDIS_RETRY_AFTER = 60.0  # после неудачного подключения к Redis не пробуем столько секунд

# KEYS[1] — sorted set; ARGV: now, window, limit, n, id. Возвращает "0" (зарезервировано)
# или строку с числом секунд, через которое освободится нужный объём.
_LUA = """
local now, window, limit, n = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local entries = redis.call('ZRANGE', KEYS[1], 0, -1, 'WITHSCORES')
local used = 0
for i = 1, #entries, 2 do used = used + tonumber(string.match(entries[i], ':(%d+)$')) end
if used + n <= limit then
  redis.call('ZADD', KEYS[1], now, ARGV[5] .. ':' .. n)
  redis.call('EXPIRE', KEYS[1], math.ceil(window) + 1)
  return '0'
end
local need, freed = used + n - limit, 0
for i = 1, #entries, 2 do
  freed = freed + tonumber(string.match(entries[i], ':(%d+)$'))
  if freed >= need then return tostring(tonumber(entries[i + 1]) + window - now) end
end
return tostring(window)
"""


class WindowBackend(Protocol):
    async def try_acquire(self, key: str, n: int, now: float, window: float, limit: int) -> float:
        """0 — зарезервировано; > 0 — сколько секунд ждать до следующей попытки."""

    async def aclose(self) -> None: ...


class MemoryWindow:
    """Тот же алгоритм, что в Lua, но в памяти процесса."""

    def __init__(self) -> None:
        self._entries: dict[str, list[tuple[float, int]]] = {}
        self._lock = asyncio.Lock()

    async def try_acquire(self, key: str, n: int, now: float, window: float, limit: int) -> float:
        async with self._lock:
            entries = [e for e in self._entries.get(key, []) if e[0] > now - window]
            used = sum(c for _, c in entries)
            if used + n <= limit:
                entries.append((now, n))
                self._entries[key] = entries
                return 0.0
            self._entries[key] = entries
            need, freed = used + n - limit, 0
            for ts, c in entries:
                freed += c
                if freed >= need:
                    return ts + window - now
            return window

    async def aclose(self) -> None:
        pass


class RedisWindow:
    def __init__(self, client: Any) -> None:
        self._redis = client
        self._script = client.register_script(_LUA)

    async def try_acquire(self, key: str, n: int, now: float, window: float, limit: int) -> float:
        res = await self._script(keys=[key], args=[now, window, limit, n, uuid.uuid4().hex])
        return float(res.decode() if isinstance(res, bytes) else res)

    async def aclose(self) -> None:
        await self._redis.aclose()


# Резерв в памяти общий на процесс — чтобы все клиенты одного воркера считали вместе
_memory = MemoryWindow()
_redis_failed_at: float | None = None


async def _default_backend() -> WindowBackend:
    global _redis_failed_at
    if _redis_failed_at is not None and time.monotonic() - _redis_failed_at < _REDIS_RETRY_AFTER:
        return _memory
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        await client.ping()
        _redis_failed_at = None
        return RedisWindow(client)
    except Exception:  # noqa: BLE001
        _redis_failed_at = time.monotonic()
        log.warning("Redis недоступен — лимит запросов Ozon считается в памяти процесса")
        return _memory


class OzonRateLimiter:
    def __init__(
        self,
        client_id: str,
        *,
        limit: int | None = None,
        window: float = GLOBAL_WINDOW_SECONDS,
        method_limits: dict[str, tuple[int, float]] | None = None,
        backend: WindowBackend | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.key = _KEY_PREFIX + client_id
        self.limit = limit or settings.OZON_RATE_LIMIT_PER_SECOND
        self.window = window
        self.method_limits = METHOD_LIMITS if method_limits is None else method_limits
        self._backend = backend
        self._clock = clock
        self._sleep = sleep if sleep is not None else asyncio.sleep

    async def check_and_acquire(self, n: int = 1, path: str | None = None) -> None:
        """Резервирует n запросов к Ozon (общий лимит кабинета и лимит метода path, если он есть);
        если лимита не хватает — ждёт, сколько нужно."""
        if n <= 0:
            return
        windows = [(self.key, self.limit, self.window)]
        if path and path in self.method_limits:
            m_limit, m_window = self.method_limits[path]
            windows.append((f"{self.key}:{path}", m_limit, m_window))
        for _, limit, _ in windows:
            if n > limit:
                raise ValueError(f"Нельзя зарезервировать {n} запросов при лимите {limit}")
        if self._backend is None:
            self._backend = await _default_backend()

        # Сначала самый строгий (поминутный) лимит метода, потом общий посекундный:
        # ожидание по методу не «сжигает» секундные слоты кабинета
        for key, limit, window in reversed(windows):
            while True:
                wait = await self._backend.try_acquire(key, n, self._clock(), window, limit)
                if wait <= 0:
                    break
                log.debug("Лимит Ozon (%d запросов за %.0f с): ждём %.2f с", limit, window, wait)
                await self._sleep(wait + 0.01)

    async def aclose(self) -> None:
        if self._backend is not None and self._backend is not _memory:
            await self._backend.aclose()
