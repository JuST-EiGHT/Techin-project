"""Проверка API-ключей Ozon тестовым запросом.

Api-Key передаётся только в заголовке запроса к Ozon: не логируется и не попадает
в тексты ошибок. Тело ответа Ozon в сообщения пользователю тоже не копируем.
"""
import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

PRODUCT_LIST_PATH = "/v3/product/list"

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


def mask_client_id(client_id: str) -> str:
    return "••••" + client_id[-4:] if len(client_id) > 4 else "••••"


def _ozon_message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("message", "")).lower()
    except ValueError:
        return ""


def _classify(r: httpx.Response) -> VerifyResult:
    if r.status_code == 200:
        return _OK
    if r.status_code == 403:
        msg = _ozon_message(r)
        return _DEACTIVATED if ("deactivat" in msg or "expired" in msg) else _FORBIDDEN
    if r.status_code in (400, 401, 404):
        return _INVALID
    if r.status_code == 429:
        return _RATE_LIMITED
    return _UNAVAILABLE


async def verify_ozon_credentials(client_id: str, api_key: str) -> VerifyResult:
    """Тестовый запрос POST /v3/product/list с limit=1."""
    try:
        async with httpx.AsyncClient(base_url=settings.OZON_API_URL, timeout=settings.OZON_TIMEOUT_SECONDS) as c:
            r = await c.post(
                PRODUCT_LIST_PATH,
                json={"filter": {"visibility": "ALL"}, "last_id": "", "limit": 1},
                headers={"Client-Id": client_id, "Api-Key": api_key},
            )
    except httpx.HTTPError as e:
        log.warning("Проверка ключа Ozon (client %s): нет ответа (%s)", mask_client_id(client_id), type(e).__name__)
        return _UNAVAILABLE

    result = _classify(r)
    log.info("Проверка ключа Ozon (client %s): HTTP %s → %s", mask_client_id(client_id), r.status_code, result.reason)
    return result
