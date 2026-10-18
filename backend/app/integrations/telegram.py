"""Telegram Bot API через python-telegram-bot 22.8 (шаг 7.3): отправка сообщения, ошибки по-русски, защита токена.

Токен бота — только в .env (TELEGRAM_BOT_TOKEN), в API он не возвращается и в журнал не попадает. Он стоит в адресе
каждого запроса (https://api.telegram.org/bot<токен>/…): httpx пишет адреса в журнал на уровне INFO (Celery работает с
--loglevel=info), а InvalidToken у python-telegram-bot содержит сам токен («The token `…` was rejected»). Поэтому:
`protect_logs` вырезает токен из любой записи журнала, `redact` — из любого текста ошибки, который мы храним или пишем.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TelegramError, TimedOut

from app.core.config import settings

log = logging.getLogger(__name__)
HIDDEN = "<токен скрыт>"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


def redact(text: str) -> str:
    token = settings.TELEGRAM_BOT_TOKEN
    return text.replace(token, HIDDEN) if token else text


_installed = False


def protect_logs() -> None:
    """Токен вырезается из каждой записи журнала — текста и трассировки — от любого логгера (у python-telegram-bot их
    много: telegram.Bot пишет адрес API с токеном на уровне DEBUG, httpx — адрес запроса на INFO). Повторный вызов — без
    второй обёртки."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            return record
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — запись с неверными аргументами: пусть её разберёт обычный Formatter
            return record
        if token in message:
            record.msg, record.args = redact(message), ()
        if record.exc_info and record.exc_info[1] is not None:
            text = logging.Formatter().formatException(record.exc_info)
            if token in text:
                record.exc_text = redact(text)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


protect_logs()


@dataclass
class SendError(Exception):
    """Telegram не принял или не получил сообщение. kind: blocked — бот остановлен или заблокирован пользователем;
    chat_not_found; retry — лимит Telegram, повторить через retry_after с; network — Telegram недоступен; token — токен не
    принят; other. message — для пользователя и журнала, без токена."""

    kind: str
    message: str
    retry_after: int | None = None

    def __str__(self) -> str:
        return self.message


def to_error(exc: TelegramError) -> SendError:
    text = redact(str(exc))
    if isinstance(exc, Forbidden):
        return SendError("blocked", "Бот не может написать: его остановили или заблокировали в Telegram. Откройте бота и "
                                    "нажмите «Запустить» или подключите Telegram заново.")
    if isinstance(exc, BadRequest):
        if "chat not found" in text.lower():
            return SendError("chat_not_found", "Чат Telegram не найден — подключите Telegram заново.")
        return SendError("other", f"Telegram не принял сообщение: {text}")
    if isinstance(exc, RetryAfter):
        wait = exc.retry_after
        seconds = int(wait.total_seconds()) if isinstance(wait, timedelta) else int(wait)
        return SendError("retry", f"Telegram просит подождать {seconds} с — отправим позже.", retry_after=seconds)
    if isinstance(exc, InvalidToken):
        return SendError("token", "Telegram не принял токен бота — проверьте TELEGRAM_BOT_TOKEN на сервере.")
    if isinstance(exc, (TimedOut, NetworkError)):
        return SendError("network", "Telegram сейчас недоступен — отправим позже.")
    return SendError("other", f"Ошибка Telegram: {text}")


class TelegramSender:
    """Отправка сообщений ботом: `async with TelegramSender() as tg: await tg.send(chat_id, html)`."""

    def __init__(self, token: str | None = None):
        extra = {"base_url": settings.TELEGRAM_API_URL} if settings.TELEGRAM_API_URL else {}
        self._bot = Bot(token or settings.TELEGRAM_BOT_TOKEN, **extra)

    async def __aenter__(self) -> "TelegramSender":
        try:
            await self._bot.initialize()  # getMe: проверяет токен
        except TelegramError as exc:
            raise to_error(exc) from None
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self._bot.shutdown()
        except TelegramError as e:  # закрытие соединений не должно ронять отправку
            log.warning("Telegram: не удалось закрыть соединение: %s", redact(str(e)))

    async def send(self, chat_id: int, text: str) -> int:
        """HTML-сообщение без превью ссылок; → message_id."""
        try:
            message = await self._bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        except TelegramError as exc:
            raise to_error(exc) from None
        return message.message_id
