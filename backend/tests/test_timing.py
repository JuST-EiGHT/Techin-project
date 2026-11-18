"""Время запроса (жалоба 18.11.2026 на медленную страницу товара): заголовок Server-Timing у каждого ответа, медленный
запрос — строка в журнале без параметров запроса."""
import logging

from app.core import timing


async def test_server_timing_header(client):
    r = await client.get("/health")
    value = r.headers.get("server-timing", "")
    assert value.startswith("app;dur=") and "db;dur=" in value and "SQL" in value


async def test_slow_request_logged_without_query(client, monkeypatch, caplog):
    monkeypatch.setattr(timing, "SLOW_SECONDS", 0.0)
    with caplog.at_level(logging.WARNING, logger="app.core.timing"):
        await client.get("/health?secret=1")
    lines = [r.getMessage() for r in caplog.records if r.name == "app.core.timing"]
    assert lines and lines[-1].startswith("медленный запрос: GET /health — ") and "secret" not in lines[-1]
