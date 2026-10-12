"""Проверка API-ключей Ozon через OzonClient.

1. POST /v1/roles — ключ действует? Заодно Ozon сообщает срок действия (expires_at).
2. POST /v3/product/list с limit=1 — у ключа есть права на чтение товаров?

Api-Key передаётся только в заголовке запроса к Ozon: не логируется и не попадает
в тексты ошибок. Текст ответа Ozon в сообщения пользователю не копируем.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.integrations.ozon.client import OzonClient, mask_client_id
from app.integrations.ozon.errors import OzonAPIError, OzonAuthError, OzonRateLimitError

__all__ = ["VerifyResult", "verify_ozon_credentials", "mask_client_id", "PRODUCT_LIST_PATH", "ROLES_PATH"]

log = logging.getLogger(__name__)

ROLES_PATH = "/v1/roles"
PRODUCT_LIST_PATH = "/v3/product/list"
# Пользователь ждёт ответа формы — без повторов и с коротким таймаутом
VERIFY_TIMEOUT_SECONDS = 15.0

Reason = Literal["ok", "invalid", "forbidden", "unavailable"]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: Reason
    message: str
    expires_at: datetime | None = None  # срок ключа по данным Ozon, если удалось узнать


_OK_MESSAGE = "Ключ работает"
_INVALID = VerifyResult(False, "invalid", "Неверный Client-Id или Api-Key")
_NO_READ = VerifyResult(
    False, "forbidden", "У ключа нет прав на чтение товаров — выдайте их в кабинете Ozon (Настройки → Seller API)"
)
_RATE_LIMITED = VerifyResult(False, "unavailable", "Ozon временно ограничил запросы, попробуйте через минуту")
_UNAVAILABLE = VerifyResult(False, "unavailable", "Ozon недоступен, попробуйте позже")


def _classify(e: OzonAPIError, *, reading_products: bool) -> VerifyResult:
    if isinstance(e, OzonAuthError):
        if e.kind in ("invalid", "deactivated"):
            return VerifyResult(False, "invalid", e.message if e.kind == "deactivated" else _INVALID.message)
        if reading_products and e.kind in ("no_role", "forbidden"):
            return _NO_READ
        return VerifyResult(False, "forbidden", e.message)
    if isinstance(e, OzonRateLimitError):
        return _RATE_LIMITED
    if e.status_code in (400, 404):
        return _INVALID
    return _UNAVAILABLE


def _parse_expires(data: Any) -> datetime | None:
    raw = data.get("expires_at") if isinstance(data, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def verify_ozon_credentials(client_id: str, api_key: str) -> VerifyResult:
    expires_at: datetime | None = None
    async with OzonClient(client_id, api_key, timeout=VERIFY_TIMEOUT_SECONDS, retry=False) as client:
        try:
            expires_at = _parse_expires(await client.request("POST", ROLES_PATH, {}))
        except OzonAuthError as e:
            if e.kind != "no_role":  # без роли на /v1/roles ключ всё ещё может читать товары
                return _log(client_id, _classify(e, reading_products=False))
        except OzonAPIError as e:
            # Ozon недоступен — проверять дальше бессмысленно; прочие 4xx от /v1/roles
            # не повод отклонять ключ — решит запрос товаров
            if isinstance(e, OzonRateLimitError) or e.status_code is None or e.status_code >= 500:
                return _log(client_id, _classify(e, reading_products=False))
        try:
            await client.request("POST", PRODUCT_LIST_PATH, {"filter": {"visibility": "ALL"}, "last_id": "", "limit": 1})
        except OzonAPIError as e:
            return _log(client_id, _classify(e, reading_products=True))
    return _log(client_id, VerifyResult(True, "ok", _OK_MESSAGE, expires_at))


def _log(client_id: str, result: VerifyResult) -> VerifyResult:
    log.info("Проверка ключа Ozon (client %s): %s", mask_client_id(client_id), result.reason)
    return result
