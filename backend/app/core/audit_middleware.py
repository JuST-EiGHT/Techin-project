"""Middleware журнала (шаг 6.1): каждый изменяющий запрос к API (POST, PUT, PATCH, DELETE) вошедшего пользователя —
запись в audit_log: метод, путь и его шаблон, HTTP-статус, IP, User-Agent, request_id.

- Тело запроса не пишем: в нём бывают пароли, API-ключи Ozon и токены банков (правило 7). Что именно изменилось в
  Ozon, пишет сервис действия — его запись связана с записью запроса общим request_id.
- Без входа (регистрация, вход, обновление токена) и с недействительным токеном (ответ 401) — не пишем: пользователя
  нет.
- Запросы, которые ничего не меняют (предпросмотр, расчёт), помечает эндпоинт — `audit.not_a_change` / `read_only`.
- Не удалось записать — запрос всё равно проходит, в лог — предупреждение.
Чистый ASGI (не BaseHTTPMiddleware): статус берём из http.response.start, запись — после ответа.
"""
import logging
import uuid
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db.session import get_db
from app.models.audit import FAILED, HTTP_ENTITY, OK, REJECTED, AuditLog
from app.services.audit import REQUEST_ID, SKIP, client_info
from app.services.security import ACCESS, InvalidTokenError, decode_token

log = logging.getLogger(__name__)

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
API_PREFIX = "/api/"


def _user_id(scope: Scope) -> uuid.UUID | None:
    header = next((v for k, v in scope.get("headers", []) if k == b"authorization"), b"").decode("latin-1")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        return uuid.UUID(decode_token(token.strip(), expected_type=ACCESS)["sub"])
    except (InvalidTokenError, KeyError, ValueError):
        return None


def _status(code: int) -> str:
    return OK if code < 400 else REJECTED if code < 500 else FAILED


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in MUTATING or not scope["path"].startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state[REQUEST_ID] = rid = uuid.uuid4()
        code = 500

        async def send_status(message: Message) -> None:
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        except Exception:
            await self._record(scope, 500, rid)
            raise
        await self._record(scope, code, rid)

    async def _record(self, scope: Scope, code: int, rid: uuid.UUID) -> None:
        if scope.get("state", {}).get(SKIP) or code == 401:
            return
        user_id = _user_id(scope)
        if user_id is None:
            return
        route = scope.get("route")
        template = getattr(route, "path", None) or scope["path"]
        try:  # журнал запросов не должен ломать сам запрос
            ip, agent = client_info(Request(scope))
            entry = AuditLog(
                user_id=user_id, created_at=datetime.now(timezone.utc), action=f"http.{scope['method'].lower()}",
                entity_type=HTTP_ENTITY, entity_id=template[:100], status=_status(code), ip=ip,
                user_agent=agent[:500] if agent else None, request_id=rid, method=scope["method"],
                path=scope["path"][:500], http_status=code,
            )
            # та же БД, что у эндпоинтов (в тестах — подменённая через dependency_overrides)
            provider = scope["app"].dependency_overrides.get(get_db, get_db)
            sessions = provider()
            db = await anext(sessions)
            try:
                db.add(entry)
                await db.commit()
            finally:
                await sessions.aclose()
        except Exception as exc:
            log.warning("Не удалось записать запрос %s %s в audit_log: %s", scope["method"], template,
                        type(exc).__name__)
