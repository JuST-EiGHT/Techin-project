"""Чёрный список отозванных токенов (по jti).

Хранится в Redis с TTL = остаток жизни токена, поэтому список не растёт бесконечно.
Если Redis недоступен (тесты, локальный запуск без docker) — используется память процесса.
"""
import logging
import time

from app.core.config import settings

log = logging.getLogger(__name__)
_PREFIX = "revoked_jti:"


class TokenBlacklist:
    def __init__(self) -> None:
        self._redis = None
        self._memory: dict[str, int] = {}
        self._redis_checked = False

    async def _client(self):
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            await client.ping()
            self._redis = client
        except Exception:  # noqa: BLE001
            log.warning("Redis недоступен — чёрный список токенов хранится в памяти")
            self._redis = None
        return self._redis

    async def revoke(self, jti: str, exp: int) -> None:
        ttl = max(1, exp - int(time.time()))
        r = await self._client()
        if r:
            await r.set(_PREFIX + jti, "1", ex=ttl)
        else:
            self._memory[jti] = exp

    async def is_revoked(self, jti: str) -> bool:
        r = await self._client()
        if r:
            return bool(await r.exists(_PREFIX + jti))
        exp = self._memory.get(jti)
        if exp is None:
            return False
        if exp < time.time():
            self._memory.pop(jti, None)
            return False
        return True


blacklist = TokenBlacklist()
