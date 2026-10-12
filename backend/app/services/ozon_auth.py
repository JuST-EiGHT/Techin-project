"""Проверка API-ключей Ozon тестовым запросом через OzonClient.

Api-Key передаётся только в заголовке запроса к Ozon: не логируется и не попадает
в тексты ошибок. Текст ответа Ozon в сообщения пользователю не копируем.
"""
import logging
from dataclasses import dataclass
from typing import Literal

from app.integrations.ozon.client import OzonClient, mask_client_id
from app.integrations.ozon.errors import OzonAPIError, OzonAuthError, OzonRateLimitError

__all__ = ["VerifyResult", "verify_ozon_credentials", "mask_client_id", "PRODUCT_LIST_PATH"]

log = logging.getLogger(__name__)

PRODUCT_LIST_PATH = "/v3/product/list"
# Пользователь ждёт ответа формы — без повторов и с коротким таймаутом
VERIFY_TIMEOUT_SECONDS = 15.0

Reason = Literal["ok", "invalid", "forbidden", "unavailable"]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: Reason
    message: str


_OK = VerifyResult(True, "ok", "Ключ работает")
_INVALID = VerifyResult(False, "invalid", "Неверный Client-Id или Api-Key")
_DEACTIVATED = VerifyResult(False, "invalid", "Ключ отключён или истёк — создайте новый в кабинете Ozon")
_FORBIDDEN = VerifyResult(
    False, "forbidden", "У ключа нет прав на чтение товаров — выдайте их в кабинете Ozon (Настройки → API-ключи)"
)
_RATE_LIMITED = VerifyResult(False, "unavailable", "Ozon временно ограничил запросы, попробуйте через минуту")
_UNAVAILABLE = VerifyResult(False, "unavailable", "Ozon недоступен, попробуйте позже")


def _classify(e: OzonAPIError) -> VerifyResult:
    if isinstance(e, OzonAuthError):
        if e.status_code == 403:
            msg = e.ozon_message.lower()
            return _DEACTIVATED if ("deactivat" in msg or "expired" in msg) else _FORBIDDEN
        return _INVALID
    if isinstance(e, OzonRateLimitError):
        return _RATE_LIMITED
    if e.status_code in (400, 404):
        return _INVALID
    return _UNAVAILABLE


async def verify_ozon_credentials(client_id: str, api_key: str) -> VerifyResult:
    """Тестовый запрос POST /v3/product/list с limit=1."""
    async with OzonClient(client_id, api_key, timeout=VERIFY_TIMEOUT_SECONDS, retry=False) as client:
        try:
            await client.request("POST", PRODUCT_LIST_PATH, {"filter": {"visibility": "ALL"}, "last_id": "", "limit": 1})
            result = _OK
        except OzonAPIError as e:
            result = _classify(e)
    log.info("Проверка ключа Ozon (client %s): %s", mask_client_id(client_id), result.reason)
    return result
