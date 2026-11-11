"""Бот: Telegram недоступен — ждёт внутри процесса с растущей паузой, а не выходит (иначе Docker перезапускает его по
кругу и каждый запуск грузит всё приложение — процессор 100 %, 11.2026). Неверный токен — выход без перезапуска."""
from telegram.error import InvalidToken, TelegramError, TimedOut

from app import bot


class FakeApp:
    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, []

    def run_polling(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


def patch(monkeypatch, app):
    monkeypatch.setattr(bot, "configured", lambda: True)
    monkeypatch.setattr(bot, "init_sentry", lambda *_: None)
    monkeypatch.setattr(bot, "build", lambda _token: app)


def test_network_errors_wait_with_backoff(monkeypatch):
    app = FakeApp([TimedOut("Timed out")] * 6 + [None])
    patch(monkeypatch, app)
    slept = []
    assert bot.main(sleep=slept.append) == 0
    assert slept == [30, 60, 120, 300, 600, 600]  # паузы растут до 10 минут
    assert all(c == {"allowed_updates": ["message"], "close_loop": False} for c in app.calls)


def test_invalid_token_stops_without_restart(monkeypatch):
    app = FakeApp([InvalidToken("bad")])
    patch(monkeypatch, app)
    slept = []
    assert bot.main(sleep=slept.append) == 0 and slept == []


def test_other_telegram_error_exits_with_failure(monkeypatch):
    patch(monkeypatch, FakeApp([TelegramError("Conflict: other getUpdates")]))
    assert bot.main(sleep=lambda _: None) == 1


def test_without_token_does_not_start(monkeypatch):
    monkeypatch.setattr(bot, "configured", lambda: False)
    monkeypatch.setattr(bot, "build", lambda _t: (_ for _ in ()).throw(AssertionError("не должен запускаться")))
    assert bot.main() == 0
