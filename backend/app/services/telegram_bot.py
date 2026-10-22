"""Команды Telegram-бота (шаг 7.3): /start <код>, /stop, /status, /test, /help — ответы бота текстом (HTML).

Без python-telegram-bot: обработчики app/bot.py только передают сюда чат и аргументы и отправляют ответ. Подключение —
только в личном чате с ботом (в группе уведомления увидели бы другие). Неверный код — не больше 5 попыток за 10 минут на
чат: шестизначный код не подобрать перебором.
"""
import html
import time
from collections import defaultdict, deque

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.engine import notifications as engine
from app.models.user import User
from app.services import anomalies, notifier, stores

MAX_TRIES, TRIES_WINDOW = 5, 600  # неверных кодов на чат за 10 минут


class Tries:
    """Неверные коды по чатам (память процесса бота)."""

    def __init__(self) -> None:
        self._by_chat: dict[int, deque[float]] = defaultdict(deque)

    def blocked(self, chat_id: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._by_chat[chat_id]
        while q and q[0] <= now - TRIES_WINDOW:
            q.popleft()
        return len(q) >= MAX_TRIES

    def failed(self, chat_id: int, now: float | None = None) -> None:
        self._by_chat[chat_id].append(time.monotonic() if now is None else now)


TRIES = Tries()


def _site(path: str) -> str:
    return html.escape(f"{settings.SITE_URL.rstrip('/')}{path}")


SETTINGS_LINK = "/dashboard/settings/notifications"
HELP = (
    "Это бот уведомлений Ozon Analytics: присылает аномалии — упали продажи, изменилась цена, кончается остаток, не "
    "пришла выплата.\n\n"
    "/start <код> — подключить: код — на сайте, «Уведомления» в меню аккаунта\n"
    "/status — что включено и сколько активных уведомлений\n"
    "/test — тестовое сообщение\n"
    "/stop — отключить уведомления"
)
PRIVATE_ONLY = "Подключить уведомления можно только в личном чате с ботом — иначе их увидят другие участники группы."


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    shown = name[0] + "***" + (name[-1] if len(name) > 2 else "") if name else "***"
    return f"{shown}@{domain}" if domain else shown


async def cmd_start(db: AsyncSession, chat_id: int, username: str | None, name: str | None, args: list[str],
                    private: bool = True, tries: Tries = TRIES) -> str:
    if not private:
        return PRIVATE_ONLY
    linked = await notifier.accounts_for_chat(db, chat_id)
    if not args:
        if linked:
            for s in linked:  # пользователь снова нажал «Запустить» после остановки бота — снова можно писать
                s.blocked_at = None
            await db.commit()
            return "Уведомления уже подключены к этому чату. /status — что включено, /stop — отключить."
        return ("Чтобы получать уведомления, получите код на сайте («Уведомления» в меню аккаунта) и отправьте его "
                "сюда: /start 123456.\n\n" + HELP)
    if tries.blocked(chat_id):
        return "Слишком много неверных кодов — попробуйте через 10 минут."
    user_id = await notifier.link_chat(db, args[0].strip(), chat_id, username, name)
    if user_id is None:
        tries.failed(chat_id)
        return ("Код не подошёл или устарел (действует 10 минут). Получите новый на сайте: «Уведомления» в меню "
                f'аккаунта — <a href="{_site(SETTINGS_LINK)}">открыть</a>.')
    user = await db.get(User, user_id)
    who = mask_email(user.email) if user else "аккаунт"
    return (f"✅ Готово: уведомления аккаунта {html.escape(who)} будут приходить сюда.\n\n"
            "Что присылать и тихие часы — в настройках на сайте. /test — тестовое сообщение, /status — что включено, "
            "/stop — отключить.")


async def cmd_stop(db: AsyncSession, chat_id: int) -> str:
    linked = await notifier.accounts_for_chat(db, chat_id)
    if not linked:
        return "Этот чат не подключён — уведомлений сюда нет."
    for s in linked:
        await notifier.unlink(db, s.user_id)
    return ("Уведомления отключены — сюда больше ничего не придёт. На сайте они остаются (колокольчик). Подключить снова — "
            f'новым кодом в <a href="{_site(SETTINGS_LINK)}">настройках</a>.')


def _prefs_text(p: engine.Prefs) -> str:
    on = [engine.CATEGORY_TITLES[c] for c, flag in ((engine.RED, p.red), (engine.YELLOW, p.yellow),
                                                     (engine.AUTO_PROMO, p.auto_promo), (engine.MONEY, p.money)) if flag]
    quiet = (f"тихие часы {engine.hhmm(p.quiet_from)}–{engine.hhmm(p.quiet_to)}" if p.quiet_enabled else "без тихих часов")
    return f"присылаем: {', '.join(on) if on else 'ничего'}; {quiet}"


async def cmd_status(db: AsyncSession, chat_id: int) -> str:
    linked = await notifier.accounts_for_chat(db, chat_id)
    if not linked:
        return "Этот чат не подключён. " + HELP.split("\n\n", 1)[1]
    lines = []
    for s in linked:
        user = await db.get(User, s.user_id)
        names = await stores.names(db, s.user_id)  # у аккаунта несколько магазинов (шаг 8.4b) — по каждому
        text = f"<b>{html.escape(mask_email(user.email) if user else 'аккаунт')}</b>: {html.escape(_prefs_text(notifier.prefs(s)))}."
        for store_id, name in names.items() or {s.user_id: ""}.items():
            c = await anomalies.counts(db, store_id)
            prefix = f"{html.escape(name)} — а" if len(names) > 1 else "А"
            text += f"\n{prefix}ктивных уведомлений: {c['active']}, без «Ок» — {c['unread']} (красных {c['red']})."
        lines.append(text)
    lines.append(f'<a href="{_site("/dashboard/alerts")}">Все уведомления →</a>')
    return "\n\n".join(lines)


async def cmd_test(db: AsyncSession, chat_id: int) -> str:
    linked = await notifier.accounts_for_chat(db, chat_id)
    if not linked:
        return "Этот чат не подключён — сначала /start с кодом с сайта."
    return engine.render_test(settings.SITE_URL, notifier.prefs(linked[0]))
