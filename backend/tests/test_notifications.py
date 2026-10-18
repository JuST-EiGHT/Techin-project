"""Уведомления в Telegram (шаг 7.3): категории, тихие часы, тексты, привязка по коду, команды бота, отправка через
python-telegram-bot на фейковом Telegram (respx отвечает как Bot API), ошибки Telegram, токен — ни в ответах, ни в журнале."""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select, update

from app import bot as bot_main
from app.core.config import settings
from app.engine import anomaly_detector as det
from app.engine import notifications as engine
from app.engine.anomaly_text import Text
from app.integrations import telegram
from app.models.anomaly import Anomaly
from app.models.notification import NotificationLog, NotificationSettings
from app.services import notifier
from app.services import telegram_bot as commands
from app.services.sync.runner import run_sync
from app.tasks.celery_app import celery_app
from tests.test_products import seed, user_id_of

TOKEN = "123456:TEST-token-never-shown"
API = f"https://api.telegram.org/bot{TOKEN}"
SITE = "https://app.example"
CHAT = 424242
ME = {"ok": True, "result": {"id": 1, "is_bot": True, "first_name": "Ozon Analytics", "username": "ozon_test_bot"}}


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class FakeTelegram:
    """Bot API для python-telegram-bot: getMe и sendMessage (форма application/x-www-form-urlencoded)."""

    def __init__(self, router: respx.MockRouter):
        self.sent: list[dict] = []
        self.reply: httpx.Response | None = None  # ответ вместо успеха: 403, 429 …
        self.me = router.post(f"{API}/getMe")
        self.me.respond(json=ME)
        router.post(f"{API}/sendMessage").side_effect = self._send
        router.post(f"{API}/setMyCommands").respond(json={"ok": True, "result": True})

    def _send(self, request: httpx.Request) -> httpx.Response:
        if self.reply is not None:
            return self.reply
        data = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.sent.append(data)
        return httpx.Response(200, json={"ok": True, "result": {
            "message_id": len(self.sent), "date": 1_790_000_000, "chat": {"id": int(data["chat_id"]), "type": "private"},
            "text": data["text"]}})

    def error(self, code: int, description: str, **params) -> None:
        body = {"ok": False, "error_code": code, "description": description}
        if params:
            body["parameters"] = params
        self.reply = httpx.Response(code, json=body)


@pytest.fixture
def tg(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "@ozon_test_bot")
    monkeypatch.setattr(settings, "SITE_URL", SITE)
    with respx.mock(assert_all_called=False) as router:
        yield FakeTelegram(router)


async def link(sessions, uid, chat_id=CHAT, **prefs) -> None:
    async with sessions() as db:
        db.add(NotificationSettings(user_id=uid, chat_id=chat_id, telegram_username="seller", linked_at=datetime.now(timezone.utc),
                                    **prefs))
        await db.commit()


async def log_rows(sessions, uid) -> list[NotificationLog]:
    async with sessions() as db:
        return list((await db.execute(select(NotificationLog).where(NotificationLog.user_id == uid)
                                      .order_by(NotificationLog.created_at))).scalars())


# ── чистые функции ──
def test_categories_do_not_overlap():
    p = engine.Prefs(red=True, yellow=False, auto_promo=False, money=True)
    assert engine.category(det.PAYOUT_MISSED, "red") == engine.MONEY and engine.category(det.CASH_GAP, "yellow") == engine.MONEY
    assert engine.category(det.PROMO_AUTO_ADDED, "yellow") == engine.AUTO_PROMO
    assert engine.category(det.PROMO_LOSS, "red") == engine.RED and engine.category(det.PRICE_CHANGE, "yellow") == engine.YELLOW
    assert engine.wanted(p, det.LOW_STOCK, "red") and not engine.wanted(p, det.LOW_STOCK, "yellow")
    assert not engine.wanted(p, det.PROMO_AUTO_ADDED, "yellow") and engine.wanted(p, det.BANK_DIFF, "yellow")


def test_quiet_hours_in_users_timezone():
    night = engine.Prefs(quiet_enabled=True, quiet_from=23 * 60, quiet_to=8 * 60, timezone="Europe/Moscow")
    msk = lambda h, m=0, d=17: datetime(2026, 10, d, h, m, tzinfo=timezone(timedelta(hours=3)))  # noqa: E731
    assert engine.quiet_until(night, msk(22, 59)) is None
    assert engine.quiet_until(night, msk(23, 30)) == datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)  # 08:00 МСК
    assert engine.quiet_until(night, msk(3)) == datetime(2026, 10, 17, 5, 0, tzinfo=timezone.utc)
    assert engine.quiet_until(night, msk(8)) is None  # конец не входит
    day = engine.Prefs(quiet_enabled=True, quiet_from=13 * 60, quiet_to=14 * 60, timezone="Asia/Vladivostok")
    vl = datetime(2026, 10, 17, 3, 30, tzinfo=timezone.utc)  # 13:30 во Владивостоке (UTC+10)
    assert engine.quiet_until(day, vl) == datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)
    assert engine.quiet_until(engine.Prefs(quiet_enabled=False), msk(3)) is None


def test_messages_html_and_links():
    item = engine.Item("red", Text("Цена выросла на 40 %: 1 000 ₽ → 1 400 ₽", "Изменили в <кабинете> & ко", "/dashboard/x?a=1&b=2"),
                       "6001 · Подшипник <NACHI>")
    one = engine.render_one(item, SITE)
    assert one.startswith("🔴 <b>Цена выросла на 40 %")
    assert "Подшипник &lt;NACHI&gt;" in one and "&lt;кабинете&gt; &amp; ко" in one
    assert '<a href="https://app.example/dashboard/x?a=1&amp;b=2">Открыть в Ozon Analytics →</a>' in one
    items = [engine.Item("yellow", Text(f"Жёлтая {i}", "", None)) for i in range(11)] + [engine.Item("red", Text("Красная", "", "/r"))]
    digest = engine.render_digest(items, SITE)
    assert "Новые уведомления: 12" in digest and "и ещё 2 — на сайте" in digest
    assert digest.index("Красная") < digest.index("Жёлтая 0")  # красные первыми
    assert f'{SITE}/dashboard/alerts' in digest
    assert len(digest) < 4096


# ── привязка и команды бота ──
async def test_link_code_and_start_command(client, tokens, sessions, tg):
    uid = await user_id_of(client, tokens)
    r = await client.post("/api/v1/notifications/link-code", headers=auth(tokens))
    assert r.status_code == 200 and TOKEN not in r.text
    body = r.json()
    code = body["code"]
    assert len(code) == 6 and code.isdigit() and body["deep_link"] == f"https://t.me/ozon_test_bot?start={code}"
    async with sessions() as db:
        assert (await db.get(NotificationSettings, uid)).link_code_hash != code  # хранится только хэш

    tries = commands.Tries()
    async with sessions() as db:
        assert await commands.cmd_start(db, -100, "x", "Группа", [code], private=False) == commands.PRIVATE_ONLY
        text = await commands.cmd_start(db, CHAT, "seller", "Радимир", [code], tries=tries)
    assert text.startswith("✅ Готово") and "s***r@example.com" in text
    settings_ = (await client.get("/api/v1/notifications/settings", headers=auth(tokens))).json()
    assert (settings_["linked"], settings_["telegram_username"], settings_["bot_username"]) == (True, "seller", "ozon_test_bot")
    assert TOKEN not in str(settings_)
    async with sessions() as db:  # код одноразовый
        again = await commands.cmd_start(db, 777, "other", None, [code], tries=tries)
    assert again.startswith("Код не подошёл")

    # новый код отменяет прежний; через 10 минут код не действует
    first = (await client.post("/api/v1/notifications/link-code", headers=auth(tokens))).json()["code"]
    second = (await client.post("/api/v1/notifications/link-code", headers=auth(tokens))).json()["code"]
    async with sessions() as db:
        assert first == second or await notifier.link_chat(db, first, 1, None, None) is None
        late = datetime.now(timezone.utc) + timedelta(minutes=11)
        assert await notifier.link_chat(db, second, 1, None, None, now=late) is None
        assert await notifier.link_chat(db, "12345a", 1, None, None) is None


async def test_wrong_codes_are_limited(sessions, tg):
    tries = commands.Tries()
    async with sessions() as db:
        for _ in range(commands.MAX_TRIES):
            assert (await commands.cmd_start(db, 5, None, None, ["000000"], tries=tries)).startswith("Код не подошёл")
        assert (await commands.cmd_start(db, 5, None, None, ["000000"], tries=tries)).startswith("Слишком много")
        assert not tries.blocked(5, now=1e12)  # через 10 минут — снова можно


async def test_status_test_stop_commands(client, tokens, sessions, tg):
    uid = await user_id_of(client, tokens)
    await link(sessions, uid, quiet_enabled=True, notify_yellow=True)
    async with sessions() as db:
        status = await commands.cmd_status(db, CHAT)
        assert "s***r@example.com" in status and "красные, жёлтые, автоакции, деньги" in status
        assert "тихие часы 23:00–08:00" in status and "Активных уведомлений: 0" in status
        assert (await commands.cmd_test(db, CHAT)).startswith("✅ <b>Тестовое сообщение")
        assert (await commands.cmd_start(db, CHAT, None, None, [])).startswith("Уведомления уже подключены")
        assert (await commands.cmd_stop(db, CHAT)).startswith("Уведомления отключены")
        assert (await commands.cmd_status(db, CHAT)).startswith("Этот чат не подключён")
        assert (await commands.cmd_stop(db, CHAT)).startswith("Этот чат не подключён")
    assert (await client.get("/api/v1/notifications/settings", headers=auth(tokens))).json()["linked"] is False


# ── отправка уведомлений ──
async def test_new_anomalies_go_to_telegram_once(client, tokens, sessions, tg):
    """Поиск аномалий (раз в час) → новые — сразу в Telegram одним сообщением; повторная проверка — без повторов."""
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    await link(sessions, uid)
    run = await run_sync("anomalies", uid, sessions)
    assert run.status == "success" and run.stats["telegram"]["sent"] == 5
    [msg] = tg.sent
    assert (int(msg["chat_id"]), msg["parse_mode"]) == (CHAT, "HTML")
    assert msg["text"].startswith("🔔 <b>Новые уведомления: 5</b>") and f'href="{SITE}/dashboard/' in msg["text"]
    assert '"is_disabled": true' in msg["link_preview_options"]
    assert {r.status for r in await log_rows(sessions, uid)} == {"sent"}

    await run_sync("anomalies", uid, sessions)
    assert len(tg.sent) == 1  # аномалии те же — второго сообщения нет


async def test_one_anomaly_detailed_and_categories(client, tokens, sessions, tg):
    """Одна аномалия — подробное сообщение. Красные выключены, автоакции включены — приходит только автодобавление."""
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    await link(sessions, uid, notify_red=False)
    await run_sync("anomalies", uid, sessions)
    [msg] = tg.sent
    assert msg["text"].startswith("🟡 <b>Ozon сам добавил товар в акцию «Осенняя распродажа»</b>")
    assert "6001-2RS.C3 · Подшипник шариковый 6001" in msg["text"] and "Открыть в Ozon Analytics" in msg["text"]


async def test_quiet_hours_then_digest_and_outdated(client, tokens, sessions, tg):
    """В тихие часы — не шлём, копим; после — одним сообщением, без тех, что уже закрылись или отмечены «Ок»."""
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    now_msk = datetime.now(timezone(timedelta(hours=3)))
    minute = now_msk.hour * 60 + now_msk.minute
    await link(sessions, uid, quiet_enabled=True, quiet_from=(minute - 60) % 1440, quiet_to=(minute + 60) % 1440)
    run = await run_sync("anomalies", uid, sessions)
    assert run.stats["telegram"] == {"queued": 5, "sent": 0, "outdated": 0, "failed": 0, "users": 0}
    assert tg.sent == []
    rows = await log_rows(sessions, uid)
    assert {r.status for r in rows} == {"pending"} and rows[0].send_after > datetime.now(timezone.utc).replace(tzinfo=None)

    async with sessions() as db:  # пока ждали: одну отметили «Ок» на сайте
        a = (await db.execute(select(Anomaly).where(Anomaly.user_id == uid, Anomaly.type == "low_stock"))).scalar_one()
        a.is_acknowledged = True
        await db.commit()
        stats = await notifier.send_due(db, now=datetime.now(timezone.utc) + timedelta(hours=2))
    assert (stats["sent"], stats["outdated"]) == (4, 1)
    [msg] = tg.sent
    assert "Новые уведомления: 4" in msg["text"] and "Товар закончился" not in msg["text"]


async def test_telegram_errors(client, tokens, sessions, tg):
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    await link(sessions, uid)
    # лимит Telegram — повтор через указанное время
    tg.error(429, "Too Many Requests: retry after 30", retry_after=30)
    run = await run_sync("anomalies", uid, sessions)
    rows = await log_rows(sessions, uid)
    assert run.stats["telegram"]["failed"] == 5 and {r.status for r in rows} == {"pending"}
    assert all(r.attempts == 1 and "подождать 30 с" in r.error for r in rows)
    # Telegram недоступен — повтор через 5 минут, после 5 попыток — не отправится
    tg.error(502, "Bad Gateway")
    async with sessions() as db:
        for i in range(4):
            await notifier.send_due(db, now=datetime.now(timezone.utc) + timedelta(hours=i + 1))
    assert {r.status for r in await log_rows(sessions, uid)} == {"failed"}

    # пользователь остановил бота — отметка «бот не может написать», сообщения не уйдут
    async with sessions() as db:
        await db.execute(update(NotificationLog).values(status="pending", attempts=0))
        await db.commit()
        tg.error(403, "Forbidden: bot was blocked by the user")
        await notifier.send_due(db, now=datetime.now(timezone.utc) + timedelta(hours=9))
    assert {r.status for r in await log_rows(sessions, uid)} == {"failed"}
    body = (await client.get("/api/v1/notifications/settings", headers=auth(tokens))).json()
    assert body["blocked"] is True
    r = await client.post("/api/v1/notifications/test", headers=auth(tokens))
    assert r.status_code == 400 and "остановили или заблокировали" in r.json()["detail"]


async def test_token_never_in_logs_errors_or_api(client, tokens, sessions, tg, caplog):
    """Telegram не принял токен: текст ошибки python-telegram-bot содержит токен — у нас его нет нигде."""
    caplog.set_level(logging.DEBUG)
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    await link(sessions, uid)
    tg.me.respond(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    run = await run_sync("anomalies", uid, sessions)
    assert run.stats["telegram"]["error"] == "Telegram не принял токен бота — проверьте TELEGRAM_BOT_TOKEN на сервере."
    rows = await log_rows(sessions, uid)
    assert {r.status for r in rows} == {"pending"} and all(TOKEN not in (r.error or "") for r in rows)
    r = await client.post("/api/v1/notifications/test", headers=auth(tokens))
    assert r.status_code == 400 and TOKEN not in r.text
    for path in ("/api/v1/notifications/settings", "/api/v1/anomalies"):
        assert TOKEN not in (await client.get(path, headers=auth(tokens))).text
    assert "api.telegram.org" in caplog.text  # запросы в журнале есть …
    assert TOKEN not in caplog.text and telegram.HIDDEN in caplog.text  # … но без токена


# ── API настроек ──
async def test_settings_api_and_test_message(client, tokens, sessions, tg):
    uid = await user_id_of(client, tokens)
    get = lambda: client.get("/api/v1/notifications/settings", headers=auth(tokens))  # noqa: E731
    patch = lambda body: client.patch("/api/v1/notifications/settings", json=body, headers=auth(tokens))  # noqa: E731
    body = (await get()).json()
    assert (body["linked"], body["notify_red"], body["notify_yellow"], body["notify_auto_promo"], body["notify_money"]) == (
        False, True, False, True, True)
    assert (body["quiet_enabled"], body["quiet_from"], body["quiet_to"], body["timezone"]) == (False, "23:00", "08:00", "Europe/Moscow")
    assert {"value": "Asia/Vladivostok", "label": "Владивосток (UTC+10)"} in body["timezones"]
    r = await patch({"notify_yellow": True, "quiet_enabled": True, "quiet_from": "22:30", "quiet_to": "07:00",
                     "timezone": "Asia/Yekaterinburg"})
    assert r.status_code == 200
    assert (r.json()["notify_yellow"], r.json()["quiet_from"], r.json()["timezone"]) == (True, "22:30", "Asia/Yekaterinburg")
    assert (await patch({"timezone": "Europe/London"})).json()["detail"] == "Часовой пояс: выберите из списка"
    r = await patch({"quiet_from": "07:00"})
    assert (r.status_code, r.json()["detail"]) == (400, "Тихие часы: начало и конец совпадают — укажите разное время")
    assert (await patch({"quiet_to": "25:00"})).status_code == 400

    r = await client.post("/api/v1/notifications/test", headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Сначала подключите Telegram — получите код и отправьте его боту.")
    async with sessions() as db:
        row = await db.get(NotificationSettings, uid)
        row.chat_id = CHAT
        await db.commit()
    r = await client.post("/api/v1/notifications/test", headers=auth(tokens))
    assert r.status_code == 200 and r.json()["message"].startswith("Тестовое сообщение отправлено")
    [msg] = tg.sent
    assert "Тестовое сообщение Ozon Analytics" in msg["text"] and "Тихие часы: 22:30–07:00" in msg["text"]
    assert (await get()).json()["last_sent_at"] is not None

    await client.post("/api/v1/notifications/unlink", headers=auth(tokens))
    assert (await get()).json()["linked"] is False


async def test_without_bot_token(client, tokens, sessions):
    """TELEGRAM_BOT_TOKEN не задан — сайт работает, в Telegram ничего не уходит, понятные ответы."""
    uid = await user_id_of(client, tokens)
    body = (await client.get("/api/v1/notifications/settings", headers=auth(tokens))).json()
    assert body["bot_configured"] is False and body["bot_username"] is None
    r = await client.post("/api/v1/notifications/link-code", headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"].startswith("Telegram-бот не настроен на сервере")
    await seed(sessions, uid)
    await link(sessions, uid)
    run = await run_sync("anomalies", uid, sessions)
    assert run.stats["created"] == 5 and run.stats["telegram"]["queued"] == 0
    assert await log_rows(sessions, uid) == []


# ── бот на python-telegram-bot ──
def update_stub(chat_id=CHAT, chat_type="private", username="seller"):
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
                           effective_user=SimpleNamespace(username=username, first_name="Радимир", last_name=None),
                           effective_message=message), message


async def test_bot_handlers(client, tokens, sessions, tg):
    uid = await user_id_of(client, tokens)
    code = (await client.post("/api/v1/notifications/link-code", headers=auth(tokens))).json()["code"]
    context = SimpleNamespace(args=[code], application=SimpleNamespace(bot_data={"sessions": sessions}))
    upd, message = update_stub()
    await bot_main.start(upd, context)
    text = message.reply_text.call_args.args[0]
    assert text.startswith("✅ Готово") and message.reply_text.call_args.kwargs["parse_mode"] == "HTML"
    for handler, starts in ((bot_main.status, "<b>s***r@example.com</b>"), (bot_main.test, "✅ <b>Тестовое"),
                            (bot_main.help_, "Это бот уведомлений"), (bot_main.stop, "Уведомления отключены")):
        upd, message = update_stub()
        await handler(upd, context)
        assert message.reply_text.call_args.args[0].startswith(starts), handler.__name__
    async with sessions() as db:
        assert (await db.get(NotificationSettings, uid)).chat_id is None


def test_bot_app_and_main(monkeypatch, caplog):
    app = bot_main.build(TOKEN)
    commands_ = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert commands_ == {"start", "stop", "status", "test", "help"}
    assert [c.command for c in bot_main.COMMANDS] == ["start", "status", "test", "stop"]
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    assert bot_main.main() == 0  # без токена — не запускается, без ошибки
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", TOKEN)
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{API}/getMe").respond(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        router.post(f"{API}/deleteWebhook").respond(json={"ok": True, "result": True})
        assert bot_main.main() == 1  # токен не принят — выходим с ошибкой
    assert "Бот остановлен: InvalidToken" in caplog.text and TOKEN not in caplog.text


def test_schedule():
    beat = celery_app.conf.beat_schedule
    assert beat["send-notifications-every-5-min"]["task"] == "tasks.send_notifications"


async def test_quiet_hours_changed_after_queueing(client, tokens, sessions, tg):
    """Тихие часы выключили — ждущие уходят сразу; включили после постановки в очередь — ждут их конца."""
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    now_msk = datetime.now(timezone(timedelta(hours=3)))
    minute = now_msk.hour * 60 + now_msk.minute
    await link(sessions, uid, quiet_enabled=True, quiet_from=(minute - 60) % 1440, quiet_to=(minute + 60) % 1440)
    await run_sync("anomalies", uid, sessions)
    assert tg.sent == []
    r = await client.patch("/api/v1/notifications/settings", json={"quiet_enabled": False}, headers=auth(tokens))
    assert r.json()["pending"] == 5
    async with sessions() as db:
        assert (await notifier.send_due(db))["sent"] == 5
    assert len(tg.sent) == 1

    async with sessions() as db:  # очередь при выключенных тихих часах, потом их включили — ночью не шлём
        await db.execute(update(NotificationLog).values(status="pending", send_after=datetime.now(timezone.utc)))
        row = await db.get(NotificationSettings, uid)
        row.quiet_enabled = True
        await db.commit()
        stats = await notifier.send_due(db)
    assert stats["sent"] == 0 and len(tg.sent) == 1
    assert {r.send_after > datetime.now(timezone.utc).replace(tzinfo=None) for r in await log_rows(sessions, uid)} == {True}


async def test_own_bot_api_server(monkeypatch):
    """TELEGRAM_API_URL — свой сервер Bot API: запросы идут туда."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "TELEGRAM_API_URL", "http://bot-api.local/bot")
    with respx.mock(assert_all_called=False) as router:
        me = router.post(f"http://bot-api.local/bot{TOKEN}/getMe").respond(json=ME)
        router.post(f"http://bot-api.local/bot{TOKEN}/sendMessage").respond(json={"ok": True, "result": {
            "message_id": 5, "date": 1_790_000_000, "chat": {"id": CHAT, "type": "private"}, "text": "x"}})
        async with telegram.TelegramSender() as sender:
            assert await sender.send(CHAT, "x") == 5
    assert me.called
