"""Ошибки в Sentry (шаг 8.4): API, воркер и расписание Celery, Telegram-бот. Без SENTRY_DSN — ничего не отправляется.

Подходит любой сервис с протоколом Sentry: sentry.io или GlitchTip (можно на своём сервере в РФ). Что не уходит наружу
(правило 7 и 152-ФЗ): тела запросов (там пароли, ключи Ozon, токены банков), заголовки и cookies, значения переменных в
трассировке, IP и email пользователя; токен Telegram-бота вырезается из всего события — он стоит в адресе запросов к
Telegram (integrations/telegram).
"""
import json
import logging

from app.core.config import settings

log = logging.getLogger(__name__)
HIDDEN = "<скрыто>"


def scrub(data: dict | None) -> dict | None:
    """Событие или «хлебная крошка» без секретов: всё, что совпадает с токеном бота, — «<скрыто>»."""
    if data is None:
        return None
    secrets = [s for s in (settings.TELEGRAM_BOT_TOKEN,) if s]
    if not secrets:
        return data
    text = json.dumps(data, ensure_ascii=False, default=str)
    if not any(s in text for s in secrets):
        return data
    for s in secrets:
        text = text.replace(s, HIDDEN)
    return json.loads(text)


def _before_send(event, _hint):
    return scrub(event)


def _before_breadcrumb(crumb, _hint):
    return scrub(crumb)


def init_sentry(component: str) -> bool:
    """Подключить Sentry для процесса (api | worker | bot). True — подключён."""
    if not settings.SENTRY_DSN:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=settings.APP_VERSION,
        send_default_pii=False,  # без IP, cookies и заголовков
        include_local_variables=False,  # в переменных бывают расшифрованные ключи Ozon и токены банков
        max_request_body_size="never",  # в телах — пароли и ключи
        traces_sample_rate=0.0,  # только ошибки, без замеров скорости
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
    )
    sentry_sdk.set_tag("component", component)
    log.info("Sentry подключён (%s)", component)
    return True
