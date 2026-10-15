"""Универсальный клиент Ozon Seller API.

Повторы (retry):
- сеть и таймауты — до 3 повторов с паузой 1, 2, 4 с;
- 429 — до 6 повторов: пауза из Retry-After (не больше 60 с) или 1, 2, 4, 8, 16, 30 с;
- 5xx — до 2 повторов через 5 с;
- 401/403 и прочие 4xx — без повторов, сразу исключение.

Лимит Ozon — 50 запросов в секунду на Client ID плюс лимиты отдельных методов
(docs/OZON_API.md, «Лимиты»): перед каждой попыткой клиент резервирует слот в
rate limiter (общий счётчик кабинета в Redis).

Api-Key живёт только в заголовке запроса: в логи, тексты ошибок и repr не попадает.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.ozon.errors import (
    OzonAPIError,
    OzonAuthError,
    OzonNetworkError,
    OzonRateLimitError,
    OzonServerError,
    OzonTimeoutError,
)
from app.integrations.ozon.rate_limiter import OzonRateLimiter

log = logging.getLogger(__name__)

NETWORK_BACKOFF = (1, 2, 4)  # секунды; длина = число повторов
SERVER_RETRY_DELAY = 5
SERVER_RETRIES = 2
RATE_LIMIT_BACKOFF = (1, 2, 4, 8, 16, 30)
RETRY_AFTER_MAX = 60

# Тексты ошибок ключа из раздела «Частые ошибки» документации Ozon → (kind, сообщение)
_AUTH_ERRORS = (
    ("invalid api-key", "invalid", "Ozon не принял API-ключ: неверный Client-Id или Api-Key"),
    ("deactivated", "deactivated", "Ключ Ozon деактивирован — создайте новый в кабинете Ozon (Настройки → Seller API)"),
    ("missing a required role", "no_role",
     "У ключа Ozon нет нужной роли для этого метода — выдайте доступ в кабинете Ozon (Настройки → Seller API)"),
    ("restricted to specific ip", "ip_restricted",
     "Ключ Ozon разрешён только для определённых IP-адресов — добавьте адрес сервиса в настройках ключа"),
)
_AUTH_BY_STATUS = {
    401: ("invalid", "Ozon не принял API-ключ: неверный Client-Id или Api-Key"),
    403: ("forbidden", "Ozon отказал в доступе: у ключа нет прав на этот метод"),
}
CIRCLE_OPEN_MESSAGE = "Ozon временно ограничил этот метод из-за нагрузки, попробуйте через несколько минут"

Sleep = Callable[[float], Awaitable[Any]]


def mask_client_id(client_id: str) -> str:
    return "••••" + client_id[-4:] if len(client_id) > 4 else "••••"


def _retry_after(r: httpx.Response) -> float | None:
    try:
        value = float(r.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return min(value, RETRY_AFTER_MAX) if value >= 0 else None


class OzonClient:
    def __init__(
        self,
        client_id: str,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        retry: bool = True,
        rate_limiter: OzonRateLimiter | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self.client_id = client_id
        self._api_key = api_key
        self._retry = retry
        self._sleep: Sleep = sleep if sleep is not None else asyncio.sleep
        self._rate_limiter = rate_limiter
        self._http = httpx.AsyncClient(
            base_url=base_url or settings.OZON_API_URL,
            timeout=timeout or settings.OZON_TIMEOUT_SECONDS,
            headers={"Client-Id": client_id, "Api-Key": api_key},
        )

    def __repr__(self) -> str:
        return f"<OzonClient client={mask_client_id(self.client_id)}>"

    async def __aenter__(self) -> "OzonClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._rate_limiter is not None:
            await self._rate_limiter.aclose()

    def _limiter(self) -> OzonRateLimiter:
        if self._rate_limiter is None:
            self._rate_limiter = OzonRateLimiter(self.client_id, sleep=self._sleep)
        return self._rate_limiter

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _ozon_message(self, r: httpx.Response) -> str:
        try:
            body = r.json()
            text = str(body.get("message", "")) if isinstance(body, dict) else ""
        except ValueError:
            text = r.text[:500]
        return self._redact(text)

    async def request(self, method: str, path: str, json: Any = None) -> Any:
        """Запрос к Ozon. Возвращает разобранный JSON или бросает OzonAPIError и наследников."""
        net_retries = server_retries = rate_retries = 0
        who = mask_client_id(self.client_id)

        while True:
            await self._limiter().check_and_acquire(1, path)

            started = time.monotonic()
            try:
                r = await self._http.request(method, path, json=json)
            except httpx.TransportError as e:
                ms = (time.monotonic() - started) * 1000
                kind = type(e).__name__
                if self._retry and net_retries < len(NETWORK_BACKOFF):
                    delay = NETWORK_BACKOFF[net_retries]
                    net_retries += 1
                    log.warning("Ozon %s %s: %s за %.0f мс (client %s), повтор %d/%d через %s с",
                                method, path, kind, ms, who, net_retries, len(NETWORK_BACKOFF), delay)
                    await self._sleep(delay)
                    continue
                log.error("Ozon %s %s: %s за %.0f мс (client %s), повторы исчерпаны", method, path, kind, ms, who)
                error = OzonTimeoutError if isinstance(e, httpx.TimeoutException) else OzonNetworkError
                # from None: текст исключений httpx в цепочку не тянем
                raise error(path=path) from None

            ms = (time.monotonic() - started) * 1000
            status = r.status_code
            if status < 300:
                log.info("Ozon %s %s → %s за %.0f мс (client %s)", method, path, status, ms, who)
                try:
                    return r.json()
                except ValueError:
                    raise OzonAPIError("Ozon вернул ответ в неожиданном формате", status_code=status, path=path) from None

            ozon_message = self._ozon_message(r)
            log.warning("Ozon %s %s → %s за %.0f мс (client %s): %s", method, path, status, ms, who, ozon_message)

            text = ozon_message.lower()
            known = next(((k, m) for marker, k, m in _AUTH_ERRORS if marker in text), None)
            if known or status in _AUTH_BY_STATUS:
                kind, message = known or _AUTH_BY_STATUS[status]
                raise OzonAuthError(message, kind=kind, status_code=status, ozon_message=ozon_message, path=path)

            if "circle is open" in text:
                # Ozon сам заблокировал метод на несколько минут — частые повторы только мешают
                raise OzonServerError(CIRCLE_OPEN_MESSAGE, status_code=status, ozon_message=ozon_message, path=path)

            if status == 429:
                if self._retry and rate_retries < len(RATE_LIMIT_BACKOFF):
                    delay = _retry_after(r)
                    if delay is None:
                        delay = RATE_LIMIT_BACKOFF[rate_retries]
                    rate_retries += 1
                    log.warning("Ozon: лимит запросов, повтор %d/%d через %s с", rate_retries, len(RATE_LIMIT_BACKOFF), delay)
                    await self._sleep(delay)
                    continue
                raise OzonRateLimitError(status_code=status, ozon_message=ozon_message, path=path)

            if status >= 500:
                if self._retry and server_retries < SERVER_RETRIES:
                    server_retries += 1
                    log.warning("Ozon: ошибка %s, повтор %d/%d через %s с", status, server_retries, SERVER_RETRIES, SERVER_RETRY_DELAY)
                    await self._sleep(SERVER_RETRY_DELAY)
                    continue
                raise OzonServerError(status_code=status, ozon_message=ozon_message, path=path)

            raise OzonAPIError(f"Ozon отклонил запрос (HTTP {status})", status_code=status, ozon_message=ozon_message, path=path)
