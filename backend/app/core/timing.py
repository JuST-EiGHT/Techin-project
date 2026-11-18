"""Сколько идёт запрос к API и сколько из этого — база (жалоба владельца 18.11.2026: страница товара открывается ~6 с на
сервере, а локально — меньше секунды). Каждому ответу — заголовок `Server-Timing` (время всего запроса и база: время и
число SQL-запросов, видно в инструментах браузера), а запрос дольше `SLOW_SECONDS` — строка в журнале API:

    медленный запрос: GET /api/v1/products/…/stats — 5.80 с, база 4.10 с (78 SQL)

На сервере: `./scripts/compose.sh logs api | grep "медленный запрос"`. Строка запроса (параметры) в журнал не пишется.
Чистый ASGI (не BaseHTTPMiddleware): заголовок добавляем в http.response.start, ответ не буферизуем.
"""
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db.session import engine

log = logging.getLogger(__name__)

SLOW_SECONDS = 1.0


@dataclass
class _Db:
    seconds: float = 0.0
    queries: int = 0


_db: ContextVar[_Db | None] = ContextVar("request_db_time", default=None)


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    conn.info.setdefault("timing_started", []).append(time.perf_counter())


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    started = conn.info.get("timing_started")
    if not started:
        return
    took = time.perf_counter() - started.pop()
    acc = _db.get()
    if acc is not None:
        acc.seconds += took
        acc.queries += 1


class TimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        acc = _Db()
        token = _db.set(acc)
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                total = (time.perf_counter() - start) * 1000
                value = f'app;dur={total:.0f}, db;dur={acc.seconds * 1000:.0f};desc="{acc.queries} SQL"'
                message.setdefault("headers", []).append((b"server-timing", value.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            took = time.perf_counter() - start
            if took >= SLOW_SECONDS:
                log.warning("медленный запрос: %s %s — %.2f с, база %.2f с (%d SQL)",
                            scope.get("method"), scope.get("path"), took, acc.seconds, acc.queries)
            _db.reset(token)
