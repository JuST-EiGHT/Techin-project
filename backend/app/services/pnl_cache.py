"""Кэш P&L на 5 минут (шаг 3.2): ключ — пользователь + параметры запроса.

Сброс — по версии пользователя: загрузка отчёта, правка себестоимости или налогового профиля,
синхронизация начислений увеличивают версию, и старые ключи больше не читаются (истекают сами).
Redis — общий для API и воркеров Celery; без Redis (тесты, локальный запуск) — память процесса.
Клиент Redis привязан к циклу событий: у задач Celery цикл свой на каждый запуск.
"""
import asyncio
import hashlib
import json
import logging
import time
import uuid

from app.core.config import settings

log = logging.getLogger(__name__)
TTL = 300
_RETRY_AFTER = 60  # Redis недоступен — следующую попытку подключения не раньше чем через минуту


class PnLCache:
    def __init__(self) -> None:
        self._client = None
        self._loop = None
        self._failed_at: float | None = None
        self._memory: dict[str, tuple[float, str]] = {}
        self._versions: dict[str, int] = {}

    async def _redis(self):
        loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is loop:
            return self._client
        if self._failed_at is not None and time.monotonic() - self._failed_at < _RETRY_AFTER:
            return None
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            await client.ping()
            self._client, self._loop, self._failed_at = client, loop, None
            return client
        except Exception:  # noqa: BLE001
            self._client, self._failed_at = None, time.monotonic()
            log.warning("Redis недоступен — кэш P&L хранится в памяти процесса")
            return None

    async def _version(self, user_id: uuid.UUID) -> int:
        key = f"pnl:ver:{user_id}"
        r = await self._redis()
        if r is not None:
            return int(await r.get(key) or 0)
        return self._versions.get(key, 0)

    async def _key(self, user_id: uuid.UUID, kind: str, params: dict) -> str:
        digest = hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
        return f"pnl:{user_id}:{await self._version(user_id)}:{kind}:{digest}"

    async def aclose(self) -> None:
        """Закрыть клиент Redis в его же цикле событий. Задачи Celery вызывают в конце каждого запуска (у каждого свой
        asyncio.run): брошенный клиент закрывался бы сборщиком мусора уже без цикла — «Event loop is closed» в журнале."""
        client, loop = self._client, self._loop
        self._client = self._loop = None
        if client is not None and loop is asyncio.get_running_loop():
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 — закрытие соединения не должно ронять задачу
                pass

    async def get(self, user_id: uuid.UUID, kind: str, params: dict) -> dict | None:
        key = await self._key(user_id, kind, params)
        r = await self._redis()
        if r is not None:
            raw = await r.get(key)
            return json.loads(raw) if raw else None
        hit = self._memory.get(key)
        if hit is None or hit[0] < time.monotonic():
            self._memory.pop(key, None)
            return None
        return json.loads(hit[1])

    async def set(self, user_id: uuid.UUID, kind: str, params: dict, value: dict) -> None:
        key = await self._key(user_id, kind, params)
        raw = json.dumps(value, default=str)
        r = await self._redis()
        if r is not None:
            await r.set(key, raw, ex=TTL)
        else:
            self._memory[key] = (time.monotonic() + TTL, raw)

    async def invalidate(self, user_id: uuid.UUID) -> None:
        key = f"pnl:ver:{user_id}"
        r = await self._redis()
        if r is not None:
            await r.incr(key)
        else:
            self._versions[key] = self._versions.get(key, 0) + 1
            prefix = f"pnl:{user_id}:"
            for k in [k for k in self._memory if k.startswith(prefix)]:
                del self._memory[k]

    def clear_memory(self) -> None:
        """Для тестов: пустой кэш в памяти."""
        self._memory.clear()
        self._versions.clear()


cache = PnLCache()
