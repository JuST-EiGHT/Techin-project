"""Чёрный список отозванных токенов (по jti).

Хранится в Redis с TTL = остаток жизни токена, поэтому список не растёт бесконечно.
Если Redis недоступен (тесты, локальный запуск без docker) — используется память процесса.

Ротация refresh-токена оставляет короткое окно (`ROTATION_GRACE_SECONDS`): тот же старый refresh, пришедший сразу после
обновления, ещё принимается. Так несколько вкладок (или браузер, восстановивший их разом), обновляющих токен одновременно,
не выкидывают человека из аккаунта: вторая вкладка получает свою пару вместо 401 и стёртых cookies (правка владельца
06.11.2026). Выход и «Завершить сеанс» закрывают сеанс — в окно старый токен уже не проходит (проверка сеанса).
"""
import logging
import time

from app.core.config import settings

log = logging.getLogger(__name__)
_PREFIX = "revoked_jti:"
_ROTATED = "rotated_jti:"
ROTATION_GRACE_SECONDS = 60


class TokenBlacklist:
    def __init__(self) -> None:
        self._redis = None
        self._memory: dict[str, int] = {}
        self._rotated: dict[str, float] = {}  # jti → до какого момента старый refresh ещё принимается (без Redis)
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


    async def mark_rotated(self, jti: str, seconds: int = ROTATION_GRACE_SECONDS) -> None:
        """Refresh обменян на новую пару — ещё `seconds` секунд его повтор не считается кражей (параллельные вкладки)."""
        r = await self._client()
        if r:
            await r.set(_ROTATED + jti, "1", ex=seconds)
        else:
            self._rotated[jti] = time.time() + seconds

    async def rotated_recently(self, jti: str) -> bool:
        r = await self._client()
        if r:
            return bool(await r.exists(_ROTATED + jti))
        until = self._rotated.get(jti)
        if until is None:
            return False
        if until < time.time():
            self._rotated.pop(jti, None)
            return False
        return True


blacklist = TokenBlacklist()
