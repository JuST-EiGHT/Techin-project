"""Уведомления в Telegram (шаг 7.3): привязка чата, что присылать, очередь с тихими часами, отправка.

Новая аномалия (services/anomalies.detect → `after_detect`) — запись в журнале `notification_log` (pending), если чат
подключён и категория включена (engine/notifications.wanted); в тихие часы — с отправкой после их конца. `send_due` —
всё, что пора отправить пользователю: одна аномалия — подробное сообщение, несколько — одно сообщение-сводка. Перед
отправкой ещё раз: аномалия не закрылась и не отмечена «Ок», категория не выключена — иначе не шлём (outdated). Ошибки
Telegram: бот заблокирован — failed и отметка в настройках; лимит Telegram — повтор через указанное время; сеть — повтор
через 5 минут, после 5 попыток — failed. Одна аномалия — одно сообщение (уникальный индекс журнала).

Магазины (шаг 8.4b): аномалия и запись журнала — магазина (user_id магазина), а чат и настройки — аккаунта человека.
Ожидающие всех магазинов аккаунта уходят одним сообщением; если магазинов несколько — с названием магазина и ссылкой,
которая откроет сайт в нём.
"""
import hashlib
import logging
import secrets
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.engine import notifications as engine
from app.engine.anomaly_text import describe
from app.integrations import telegram
from app.models.anomaly import Anomaly
from app.models.notification import ANOMALY, FAILED, OUTDATED, PENDING, SENT, TEST, NotificationLog, NotificationSettings
from app.models.product import Product
from app.schemas.sync import utc
from app.services import stores
from app.services.sync.finance import today_msk

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RETRY_DELAY = timedelta(minutes=5)

SenderFactory = Callable[[], telegram.TelegramSender]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotifyError(Exception):
    """Текст — для пользователя."""


# ── настройки ──
async def get_settings(db: AsyncSession, user_id: uuid.UUID) -> NotificationSettings:
    """Для чтения: строки нет — значения по умолчанию (в сессию не добавляется)."""
    return await db.get(NotificationSettings, user_id) or NotificationSettings(
        user_id=user_id, notify_red=True, notify_yellow=False, notify_auto_promo=True, notify_money=True,
        quiet_enabled=False, quiet_from=23 * 60, quiet_to=8 * 60, timezone="Europe/Moscow",
    )


async def _row(db: AsyncSession, user_id: uuid.UUID) -> NotificationSettings:
    row = await db.get(NotificationSettings, user_id)
    if row is None:
        row = await get_settings(db, user_id)
        db.add(row)
    return row


def prefs(s: NotificationSettings) -> engine.Prefs:
    return engine.Prefs(s.notify_red, s.notify_yellow, s.notify_auto_promo, s.notify_money, s.quiet_enabled,
                        s.quiet_from, s.quiet_to, s.timezone)


FIELDS = ("notify_red", "notify_yellow", "notify_auto_promo", "notify_money", "quiet_enabled", "quiet_from", "quiet_to",
          "timezone")


async def update_settings(db: AsyncSession, user_id: uuid.UUID, changes: dict) -> NotificationSettings:
    row = await _row(db, user_id)
    after = {f: changes.get(f, getattr(row, f)) for f in FIELDS}
    if after["timezone"] not in engine.OFFSETS:
        raise NotifyError("Часовой пояс: выберите из списка")
    if after["quiet_enabled"] and after["quiet_from"] == after["quiet_to"]:
        raise NotifyError("Тихие часы: начало и конец совпадают — укажите разное время")
    for f in FIELDS:
        setattr(row, f, after[f])
    # тихие часы изменили — ждущие их конца сообщения перепланировать (повтор после сбоя — как был)
    now = _now()
    until = engine.quiet_until(prefs(row), now) or now
    for r in (await db.execute(select(NotificationLog).where(
        NotificationLog.user_id == user_id, NotificationLog.status == PENDING, NotificationLog.attempts == 0,
    ))).scalars():
        r.send_after = until
    await db.commit()
    return row


# ── привязка чата ──
def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def new_link_code(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> tuple[str, datetime]:
    """Шестизначный код на 10 минут, одноразовый; прежний код пользователя перестаёт действовать. Хранится только хэш."""
    if not telegram.configured():
        raise NotifyError("Telegram-бот не настроен на сервере — уведомления пока только на сайте (колокольчик).")
    now = now or _now()
    row = await _row(db, user_id)
    for _ in range(20):  # код не должен совпасть с действующим кодом другого пользователя
        code = f"{secrets.randbelow(10 ** 6):06d}"
        taken = await db.scalar(select(NotificationSettings.user_id).where(
            NotificationSettings.link_code_hash == _hash(code), NotificationSettings.link_code_expires_at > now,
            NotificationSettings.user_id != user_id,
        ))
        if taken is None:
            break
    row.link_code_hash, row.link_code_expires_at = _hash(code), now + CODE_TTL
    await db.commit()
    return code, now + CODE_TTL


async def link_chat(
    db: AsyncSession, code: str, chat_id: int, username: str | None, name: str | None, now: datetime | None = None,
) -> uuid.UUID | None:
    """/start <код> в боте: действующий код — аккаунт привязывается к этому чату (код больше не действует)."""
    now = now or _now()
    if not (len(code) == 6 and code.isdigit()):
        return None
    row = (await db.execute(select(NotificationSettings).where(
        NotificationSettings.link_code_hash == _hash(code), NotificationSettings.link_code_expires_at > now,
    ))).scalar_one_or_none()
    if row is None:
        return None
    row.chat_id, row.telegram_username, row.telegram_name = chat_id, (username or None), (name or None)
    row.linked_at, row.blocked_at = now, None
    row.link_code_hash = row.link_code_expires_at = None
    await db.commit()
    return row.user_id


async def accounts_for_chat(db: AsyncSession, chat_id: int) -> list[NotificationSettings]:
    return list((await db.execute(select(NotificationSettings).where(NotificationSettings.chat_id == chat_id))).scalars())


async def unlink(db: AsyncSession, user_id: uuid.UUID) -> None:
    row = await db.get(NotificationSettings, user_id)
    if row is not None:
        row.chat_id = row.telegram_username = row.telegram_name = row.linked_at = row.blocked_at = None
        await _drop_pending(db, user_id, "Telegram отключён")
        await db.commit()


async def _drop_pending(db: AsyncSession, user_id: uuid.UUID, reason: str) -> None:
    for r in (await db.execute(select(NotificationLog).where(
        NotificationLog.user_id.in_(await stores.store_ids(db, user_id)), NotificationLog.status == PENDING,
    ))).scalars():
        r.status, r.error = FAILED, reason


# ── очередь ──
async def queue(db: AsyncSession, user_id: uuid.UUID, anomaly_ids: list, now: datetime | None = None) -> int:
    """Новые аномалии магазина → ожидающие отправки (только подключённым — чат аккаунта владельца — и только включённые
    категории). Коммит — здесь."""
    if not anomaly_ids or not telegram.configured():
        return 0
    s = await db.get(NotificationSettings, await stores.account_id(db, user_id))
    if s is None or s.chat_id is None:
        return 0
    now = now or _now()
    p = prefs(s)
    send_after = engine.quiet_until(p, now) or now
    ids = [uuid.UUID(str(i)) for i in anomaly_ids]
    already = set((await db.execute(select(NotificationLog.anomaly_id).where(NotificationLog.anomaly_id.in_(ids)))).scalars())
    added = 0
    for a in (await db.execute(select(Anomaly).where(Anomaly.id.in_(ids), Anomaly.user_id == user_id))).scalars():
        if a.id in already or not engine.wanted(p, a.type, a.severity):
            continue
        db.add(NotificationLog(user_id=user_id, anomaly_id=a.id, kind=ANOMALY, status=PENDING, created_at=now,
                               send_after=send_after))
        added += 1
    try:
        await db.commit()
    except IntegrityError:  # та же аномалия уже в очереди (параллельный запуск) — второй раз не шлём
        await db.rollback()
        return 0
    return added


async def _items(db: AsyncSession, rows: list[NotificationLog], store_names: dict[uuid.UUID, str] | None = None,
                 ) -> dict[uuid.UUID, tuple[Anomaly, engine.Item]]:
    """Тексты аномалий; store_names — подписать магазин (у аккаунта их несколько)."""
    anomalies = {a.id: a for a in (await db.execute(select(Anomaly).where(
        Anomaly.id.in_([r.anomaly_id for r in rows if r.anomaly_id])))).scalars()}
    products = {p.id: p for p in (await db.execute(select(Product).where(
        Product.id.in_({a.product_id for a in anomalies.values() if a.product_id})))).scalars()} if anomalies else {}
    today = today_msk()
    out = {}
    for a in anomalies.values():
        product = products.get(a.product_id)
        name = (product.name or product.offer_id) if product else None
        text = describe(a.type, a.severity, a.metric_before, a.metric_after, a.threshold, a.details, a.product_id, name, today)
        label = f"{product.offer_id} · {product.name}" if product and product.name else (product.offer_id if product else None)
        store = (store_names or {}).get(a.user_id)
        out[a.id] = (a, engine.Item(a.severity, text, label, store, str(a.user_id) if store else None))
    return out


def _fail_or_retry(rows: list[NotificationLog], err: telegram.SendError, now: datetime) -> None:
    for r in rows:
        r.attempts += 1
        r.error = err.message
        if err.kind in ("blocked", "chat_not_found"):
            r.status = FAILED
        elif err.kind == "retry":
            r.send_after = now + timedelta(seconds=(err.retry_after or 1) + 1)
        elif r.attempts >= MAX_ATTEMPTS:
            r.status = FAILED
        else:
            r.send_after = now + RETRY_DELAY


async def _deliver(db: AsyncSession, tg: telegram.TelegramSender, s: NotificationSettings, rows: list[NotificationLog],
                   now: datetime) -> dict:
    """Ожидающие одного аккаунта (всех его магазинов) → одно сообщение (или ничего, если всё устарело)."""
    stats = {"sent": 0, "outdated": 0, "failed": 0}
    p = prefs(s)
    names = await stores.names(db, s.user_id)
    found = await _items(db, rows, names if len(names) > 1 else None)
    ready = []
    for r in rows:
        a, item = found.get(r.anomaly_id, (None, None))
        if a is None or a.resolved_at is not None or a.is_acknowledged or not engine.wanted(p, a.type, a.severity):
            r.status, r.error = OUTDATED, "Закрылась, отмечена «Ок» или категория выключена до отправки"
            stats["outdated"] += 1
        else:
            ready.append((r, item))
    if not ready:
        return stats
    site = settings.SITE_URL
    text = engine.render_one(ready[0][1], site) if len(ready) == 1 else engine.render_digest([i for _, i in ready], site)
    try:
        await tg.send(s.chat_id, text)
    except telegram.SendError as err:
        _fail_or_retry([r for r, _ in ready], err, now)
        if err.kind == "blocked":
            s.blocked_at = now
        elif err.kind == "chat_not_found":
            s.chat_id = None
        stats["failed"] += len(ready)
        log.warning("Telegram: пользователю %s не отправлено (%s): %s", s.user_id, err.kind, err.message)
        return stats
    for r, _ in ready:
        r.status, r.sent_at, r.chat_id, r.attempts = SENT, now, s.chat_id, r.attempts + 1
    s.blocked_at = None
    stats["sent"] += len(ready)
    return stats


async def send_due(
    db: AsyncSession, user_id: uuid.UUID | None = None, now: datetime | None = None,
    sender: SenderFactory = telegram.TelegramSender,
) -> dict:
    """Всё, что пора отправить (одному аккаунту — по любому его магазину — или всем): по одному сообщению на аккаунт.
    Коммит — здесь."""
    stats = {"sent": 0, "outdated": 0, "failed": 0, "users": 0}
    if not telegram.configured():
        return stats
    now = now or _now()
    q = select(NotificationLog).where(NotificationLog.status == PENDING, NotificationLog.send_after <= now)
    if user_id is not None:
        q = q.where(NotificationLog.user_id.in_(await stores.store_ids(db, await stores.account_id(db, user_id))))
    rows_ = list((await db.execute(q.order_by(NotificationLog.created_at))).scalars())
    accounts = await stores.accounts_for(db, {r.user_id for r in rows_})
    by_user: dict[uuid.UUID, list[NotificationLog]] = defaultdict(list)
    for r in rows_:
        by_user[accounts.get(r.user_id, r.user_id)].append(r)
    if not by_user:
        return stats
    try:
        async with sender() as tg:
            for uid, rows in by_user.items():
                s = await db.get(NotificationSettings, uid)
                if s is None or s.chat_id is None:
                    for r in rows:
                        r.status, r.error = FAILED, "Telegram отключён"
                    continue
                until = engine.quiet_until(prefs(s), now)
                if until is not None:  # тихие часы включили после постановки в очередь — ждём их конца
                    for r in rows:
                        r.send_after = until
                    continue
                part = await _deliver(db, tg, s, rows, now)
                stats["users"] += 1
                for k in ("sent", "outdated", "failed"):
                    stats[k] += part[k]
                await db.commit()
    except telegram.SendError as err:  # бот не запустился (токен, сеть) — всё ожидающее повторим позже
        log.warning("Telegram недоступен (%s): %s", err.kind, err.message)
        for rows in by_user.values():
            _fail_or_retry([r for r in rows if r.status == PENDING], err, now)
        stats["error"] = err.message
    await db.commit()
    return stats


async def after_detect(db: AsyncSession, user_id: uuid.UUID, new_ids: list,
                       sender: SenderFactory = telegram.TelegramSender) -> dict:
    """После поиска аномалий: новые — в очередь, и сразу отправить, что пора (не в тихие часы)."""
    queued = await queue(db, user_id, new_ids)
    return {"queued": queued, **await send_due(db, user_id, sender=sender)}


async def send_test(db: AsyncSession, user_id: uuid.UUID, sender: SenderFactory = telegram.TelegramSender) -> datetime:
    """Тестовое сообщение сейчас (тихие часы не действуют). Ошибка — NotifyError с понятным текстом."""
    if not telegram.configured():
        raise NotifyError("Telegram-бот не настроен на сервере — уведомления пока только на сайте (колокольчик).")
    s = await db.get(NotificationSettings, user_id)
    if s is None or s.chat_id is None:
        raise NotifyError("Сначала подключите Telegram — получите код и отправьте его боту.")
    now = _now()
    row = NotificationLog(user_id=user_id, kind=TEST, status=PENDING, chat_id=s.chat_id, created_at=now, send_after=now)
    db.add(row)
    try:
        async with sender() as tg:
            await tg.send(s.chat_id, engine.render_test(settings.SITE_URL, prefs(s)))
    except telegram.SendError as err:
        row.status, row.error, row.attempts = FAILED, err.message, 1
        if err.kind == "blocked":
            s.blocked_at = now
        elif err.kind == "chat_not_found":
            s.chat_id = None
        await db.commit()
        raise NotifyError(err.message) from None
    row.status, row.sent_at, row.attempts = SENT, now, 1
    s.blocked_at = None
    await db.commit()
    return now


async def last_sent(db: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """Последнее отправленное аккаунту — по всем его магазинам."""
    at = await db.scalar(select(NotificationLog.sent_at).where(
        NotificationLog.user_id.in_(await stores.store_ids(db, user_id)), NotificationLog.status == SENT,
    ).order_by(NotificationLog.sent_at.desc()).limit(1))
    return utc(at)


async def pending_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return await db.scalar(select(func.count()).select_from(NotificationLog).where(
        NotificationLog.user_id.in_(await stores.store_ids(db, user_id)), NotificationLog.status == PENDING,
    ))
