"""Telegram-бот уведомлений (шаг 7.3): `python -m app.bot` — отдельный сервис bot в docker-compose.

python-telegram-bot 22.8: Application с долгим опросом (run_polling) и командами /start <код>, /stop, /status, /test,
/help; ответы — services/telegram_bot. Бот только привязывает чат и отвечает на команды; уведомления об аномалиях
отправляет воркер Celery (services/notifier). Без TELEGRAM_BOT_TOKEN — не запускается и выходит без ошибки. Токен в журнал
не попадает (integrations/telegram.protect_logs, тексты ошибок — redact).
"""
import logging
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telegram import BotCommand, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.core.config import settings
from app.core.monitoring import init_sentry
from app.integrations.telegram import NO_PREVIEW, configured, protect_logs, redact
from app.services import telegram_bot as commands

log = logging.getLogger("app.bot")

COMMANDS = [
    BotCommand("start", "Подключить уведомления: /start и код с сайта"),
    BotCommand("status", "Что включено и сколько активных уведомлений"),
    BotCommand("test", "Тестовое сообщение"),
    BotCommand("stop", "Отключить уведомления"),
]


def _sessions(context: ContextTypes.DEFAULT_TYPE) -> async_sessionmaker[AsyncSession]:
    return context.application.bot_data["sessions"]


async def _reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    name = " ".join(p for p in (user.first_name, user.last_name) if p) if user else None
    async with _sessions(context)() as db:
        text = await commands.cmd_start(db, chat.id, user.username if user else None, name, list(context.args or []),
                                        private=chat.type == ChatType.PRIVATE)
    await _reply(update, text)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _sessions(context)() as db:
        await _reply(update, await commands.cmd_stop(db, update.effective_chat.id))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _sessions(context)() as db:
        await _reply(update, await commands.cmd_status(db, update.effective_chat.id))


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _sessions(context)() as db:
        await _reply(update, await commands.cmd_test(db, update.effective_chat.id))


async def help_(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, commands.HELP)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбой обработчика: в журнал — без токена и без трассировки с текстом запроса."""
    log.error("Бот: ошибка при обработке сообщения: %s: %s", type(context.error).__name__, redact(str(context.error)))


async def _init(app: Application) -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    app.bot_data["engine"] = engine
    app.bot_data["sessions"] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await app.bot.set_my_commands(COMMANDS)
    except TelegramError as e:  # меню команд — удобство, без него бот работает
        log.warning("Бот: не удалось задать меню команд: %s", redact(str(e)))


async def _close(app: Application) -> None:
    engine = app.bot_data.get("engine")  # запуск не удался (например, токен не принят) — БД не открывали
    if engine is not None:
        await engine.dispose()


def build(token: str) -> Application:
    builder = Application.builder().token(token).post_init(_init).post_shutdown(_close)
    if settings.TELEGRAM_API_URL:  # свой сервер Bot API или фейк для проверки
        builder = builder.base_url(settings.TELEGRAM_API_URL)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("help", help_))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, help_))
    app.add_error_handler(on_error)
    return app


def main() -> int:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    protect_logs()
    if not configured():
        log.warning("TELEGRAM_BOT_TOKEN не задан — бот не запущен (уведомления только на сайте)")
        return 0
    init_sentry("bot")
    try:
        build(settings.TELEGRAM_BOT_TOKEN).run_polling(allowed_updates=["message"])
    except TelegramError as e:  # например, токен не принят — текст ошибки PTB содержит токен
        log.error("Бот остановлен: %s: %s", type(e).__name__, redact(str(e)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
