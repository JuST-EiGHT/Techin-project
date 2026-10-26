"""Уведомления (шаги 7.3, 10.25): настройки по разделам и каналам (аккаунт), пороги предупреждений (магазин), код
привязки Telegram, отключение, тестовое сообщение. Токен бота в ответах не бывает (правило 7)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.engine import notifications as engine
from app.integrations import telegram
from app.models.notification import NotificationSettings
from app.models.user import User
from app.schemas.notifications import (
    Channel,
    LinkCode,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    SectionSettings,
    TestResult,
    ThresholdsOut,
    ThresholdsUpdate,
    TimezoneOption,
)
from app.schemas.sync import utc
from app.services import notifier
from app.services import notify_thresholds as thresholds_svc
from app.services import user_settings
from app.services.auth import get_current_user
from app.services.stores import get_current_store

router = APIRouter(prefix="/notifications", tags=["notifications"])

TIMEZONES = [TimezoneOption(value=name, label=f"{label} (UTC+{hours})") for name, label, hours in engine.TIMEZONES]


def _bot_username() -> str | None:
    return (settings.TELEGRAM_BOT_USERNAME.lstrip("@") or None) if telegram.configured() else None


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _channels(s: NotificationSettings) -> list[Channel]:
    linked = s.chat_id is not None
    tg_note = ("Бот на сервере ещё не подключён — уведомления только на сайте." if not telegram.configured() else
               (f"Приходят в @{s.telegram_username}." if s.telegram_username else "Подключён.") if linked else
               "Не подключён — подключите в «Интеграциях», и выбранные разделы начнут приходить в Telegram.")
    return [
        Channel(key="service", title="В сервисе", available=True, connected=True,
                note="Всегда: колокольчик в шапке, «Требует внимания» на главной и страница «Уведомления»."),
        Channel(key="telegram", title="Telegram", available=telegram.configured(), connected=linked, note=tg_note),
        Channel(key="email", title="E-mail", available=False, connected=False,
                note="Скоро — когда подключим почту: письма о важном и сводка раз в день."),
    ]


async def _response(db: AsyncSession, s: NotificationSettings) -> NotificationSettingsResponse:
    sections = notifier.sections(s)
    return NotificationSettingsResponse(
        bot_configured=telegram.configured(), bot_username=_bot_username(), linked=s.chat_id is not None,
        telegram_username=s.telegram_username, telegram_name=s.telegram_name, linked_at=utc(s.linked_at),
        blocked=s.blocked_at is not None and s.chat_id is not None,
        sections=[SectionSettings(key=x.key, title=x.title, hint=x.hint, severities=list(x.severities), soon=x.soon,
                                  **sections[x.key].as_dict()) for x in engine.SECTIONS],
        channels=_channels(s), digest_at=engine.hhmm(9 * 60 if s.digest_at is None else s.digest_at),
        quiet_enabled=s.quiet_enabled, quiet_from=engine.hhmm(s.quiet_from),
        quiet_to=engine.hhmm(s.quiet_to), timezone=s.timezone, timezones=TIMEZONES,
        pending=await notifier.pending_count(db, s.user_id), last_sent_at=await notifier.last_sent(db, s.user_id),
    )


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _response(db, await notifier.get_settings(db, user.id))


@router.patch("/settings", response_model=NotificationSettingsResponse)
async def update_settings(
    data: NotificationSettingsUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    for key in ("quiet_from", "quiet_to", "digest_at"):
        if key in changes:
            changes[key] = _minutes(changes[key])
    try:
        return await _response(db, await notifier.update_settings(db, user.id, changes))
    except notifier.NotifyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/link-code", response_model=LinkCode)
async def link_code(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Шестизначный код на 10 минут: отправить боту «/start <код>» или открыть ссылку — бот привяжет этот чат."""
    try:
        code, expires = await notifier.new_link_code(db, user.id)
    except notifier.NotifyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    bot = _bot_username()
    return LinkCode(code=code, expires_at=expires, bot_username=bot,
                    deep_link=f"https://t.me/{bot}?start={code}" if bot else None)


@router.post("/unlink", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Отключить Telegram: сюда больше ничего не придёт, ожидающие сообщения не уйдут. Настройки сохраняются."""
    await notifier.unlink(db, user.id)


@router.post("/test", response_model=TestResult)
async def send_test(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Тестовое сообщение сейчас — тихие часы не действуют."""
    try:
        at = await notifier.send_test(db, user.id)
    except notifier.NotifyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return TestResult(sent_at=at, message="Тестовое сообщение отправлено — проверьте Telegram.")


# ── пороги предупреждений (магазин) ──
@router.get("/thresholds", response_model=ThresholdsOut)
async def get_thresholds(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Пороги предупреждений по разделам — текущего магазина (до 10.25 — «Настройки → Аномалии и порог маржи»)."""
    return thresholds_svc.out(await user_settings.get_settings(db, user.id))


@router.patch("/thresholds", response_model=ThresholdsOut)
async def update_thresholds(data: ThresholdsUpdate, user: User = Depends(get_current_store),
                            db: AsyncSession = Depends(get_db)):
    """Изменить пороги (не переданные не меняются) или вернуть все по умолчанию. Маржа на страницах — сразу, аномалии —
    при следующей проверке."""
    try:
        row = await thresholds_svc.update(db, user.id, data.values, data.reset)
    except thresholds_svc.ThresholdError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return thresholds_svc.out(row)
