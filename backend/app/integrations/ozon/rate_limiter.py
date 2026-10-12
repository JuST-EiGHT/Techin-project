"""Счётчик операций с товарами для лимита Ozon (30 000 в минуту на кабинет).

Скользящее окно 60 с: каждая резервация — запись (время, число товаров) в sorted set
Redis `ozon:ops:<client_id>`. Проверка и запись атомарны (Lua-скрипт), поэтому
несколько воркеров Celery не превысят лимит вместе.

Ozon отклоняет запрос ЦЕЛИКОМ, если товаров в нём больше остатка лимита, поэтому
резервируем сразу всё n_items: не хватает — ждём, пока освободится нужное
количество, а не отправляем «сколько влезет».

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

WINDOW_SECONDS = 60.0
_KEY_PREFIX = "ozon:ops:"
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
        window: float = WINDOW_SECONDS,
        backend: WindowBackend | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.key = _KEY_PREFIX + client_id
        self.limit = limit or settings.OZON_RATE_LIMIT_PER_MINUTE
        self.window = window
        self._backend = backend
        self._clock = clock
        self._sleep = sleep if sleep is not None else asyncio.sleep

    async def check_and_acquire(self, n_items: int) -> None:
        """Резервирует n_items операций; если лимита не хватает — ждёт, сколько нужно."""
        if n_items <= 0:
            return
        if n_items > self.limit:
            # такой запрос Ozon отклонит всегда — вызывающий код должен бить на батчи
            raise ValueError(f"В одном запросе {n_items} товаров — больше лимита Ozon {self.limit} в минуту")
        if self._backend is None:
            self._backend = await _default_backend()

        while True:
            wait = await self._backend.try_acquire(self.key, n_items, self._clock(), self.window, self.limit)
            if wait <= 0:
                return
            log.info("Лимит Ozon: нужно %d операций, ждём %.1f с", n_items, wait)
            await self._sleep(wait + 0.01)

    async def aclose(self) -> None:
        if self._backend is not None and self._backend is not _memory:
            await self._backend.aclose()
