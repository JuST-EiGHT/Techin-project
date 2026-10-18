"""Лимит неудачных попыток входа и ввода кода приглашения (шаг 8.4).

Счётчики с окном: вход — пара «IP + email» (LOGIN_MAX_ATTEMPTS) и IP на любые email (LOGIN_MAX_ATTEMPTS_PER_IP) за
LOGIN_WINDOW_MINUTES; неверный код приглашения — IP (INVITE_MAX_ATTEMPTS_PER_IP) за час. Сверх лимита — 429 до конца окна,
пароль при этом не проверяется. Удачный вход обнуляет счётчик пары. Только неудачные попытки: хозяин аккаунта, вошедший с
первого раза, лимита не видит; подбирающий чужой email с другого IP хозяина не блокирует.

Счётчики — в Redis (общие для всех процессов API); Redis недоступен (тесты, запуск без Docker) — память процесса.
IP — первый адрес X-Forwarded-For: его ставит Nginx ($remote_addr), сервер Next передаёт дальше (services/audit.client_info).
"""
import logging
import math
import time

from app.core.config import settings

log = logging.getLogger(__name__)
INVITE_WINDOW = 3600


class Counters:
    def __init__(self) -> None:
        self._redis = None
        self._checked = False
        self._memory: dict[str, tuple[int, float]] = {}  # ключ → (сколько, когда окно закончится)

    async def _client(self):
        if self._checked:
            return self._redis
        self._checked = True
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            await client.ping()
            self._redis = client
        except Exception:  # noqa: BLE001
            log.warning("Redis недоступен — счётчики попыток входа хранятся в памяти процесса")
            self._redis = None
        return self._redis

    async def get(self, key: str) -> tuple[int, int]:
        """Сколько попыток в текущем окне и сколько секунд до его конца."""
        r = await self._client()
        if r:
            count, ttl = await r.get(key), await r.ttl(key)
            return (int(count), max(ttl, 1)) if count is not None else (0, 0)
        count, until = self._memory.get(key, (0, 0.0))
        left = until - time.time()
        if left <= 0:
            self._memory.pop(key, None)
            return 0, 0
        return count, math.ceil(left)

    async def hit(self, key: str, window: int) -> None:
        r = await self._client()
        if r:
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, window)
            return
        count, _ = await self.get(key)
        until = self._memory[key][1] if count else time.time() + window
        self._memory[key] = (count + 1, until)

    async def delete(self, key: str) -> None:
        r = await self._client()
        if r:
            await r.delete(key)
        else:
            self._memory.pop(key, None)

    async def delete_matching(self, prefix: str, suffix: str) -> int:
        r = await self._client()
        if r:
            keys = [k async for k in r.scan_iter(match=f"{prefix}*{suffix}")]
            if keys:
                await r.delete(*keys)
            return len(keys)
        keys = [k for k in self._memory if k.startswith(prefix) and k.endswith(suffix)]
        for k in keys:
            del self._memory[k]
        return len(keys)

    def clear_memory(self) -> None:
        self._memory.clear()


counters = Counters()


def _ip(ip: str | None) -> str:
    return ip or "unknown"


def _pair_key(email: str, ip: str | None) -> str:
    return f"login:pair:{_ip(ip)}:{email.lower()}"


def _ip_key(ip: str | None) -> str:
    return f"login:ip:{_ip(ip)}"


def _invite_key(ip: str | None) -> str:
    return f"invite:ip:{_ip(ip)}"


def wait_message(seconds: int, what: str = "входа") -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return f"Слишком много неудачных попыток {what}. Попробуйте через {minutes} мин."


async def login_wait(email: str, ip: str | None) -> int:
    """Сколько секунд ждать до следующей попытки входа (0 — можно)."""
    pair, pair_left = await counters.get(_pair_key(email, ip))
    per_ip, ip_left = await counters.get(_ip_key(ip))
    wait = 0
    if pair >= settings.LOGIN_MAX_ATTEMPTS:
        wait = max(wait, pair_left)
    if per_ip >= settings.LOGIN_MAX_ATTEMPTS_PER_IP:
        wait = max(wait, ip_left)
    return wait


async def login_failed(email: str, ip: str | None) -> None:
    window = settings.LOGIN_WINDOW_MINUTES * 60
    await counters.hit(_pair_key(email, ip), window)
    await counters.hit(_ip_key(ip), window)


async def login_succeeded(email: str, ip: str | None) -> None:
    await counters.delete(_pair_key(email, ip))


async def reset_login(email: str) -> int:
    """Снять блокировку входа для email со всех IP (команда администратора при сбросе пароля)."""
    return await counters.delete_matching("login:pair:", f":{email.lower()}")


async def invite_wait(ip: str | None) -> int:
    count, left = await counters.get(_invite_key(ip))
    return left if count >= settings.INVITE_MAX_ATTEMPTS_PER_IP else 0


async def invite_failed(ip: str | None) -> None:
    await counters.hit(_invite_key(ip), INVITE_WINDOW)
