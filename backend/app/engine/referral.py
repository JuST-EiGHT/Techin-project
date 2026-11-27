"""Реферальная программа (шаг 11.7): тем, кто оставил отзыв, — личная ссылка с UTM-метками; друзья купили по ней —
награда (11.8–11.9). Чистый модуль: код участника, метки, проверка настроек, статус по событиям.

Код участника идёт в utm_content (`ref_<код>`): в отчёте Ozon «по заказам» он есть у каждого заказа (колонка Content) —
по нему заказ найдёт участника (11.8). Остальные метки — общие у программы: utm_campaign = префикс магазина + название
кампании программы, utm_source и utm_medium — настройки программы."""
import re
import secrets
from dataclasses import dataclass

from app.engine import traffic_links as tl

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # без 0 / o, 1 / l / i — код читают и переписывают люди
CODE_LENGTH = 6
CONTENT_PREFIX = "ref_"
MAX_NAME = 200

DEFAULT_CAMPAIGN = "friends"
DEFAULT_SOURCE = "referral"
DEFAULT_MEDIUM = "friend"

PRODUCT, STORE = "product", "store"  # куда ведёт ссылка участника

# где отправили ссылку: продавец отправляет сам и отмечает (решение владельца 27.11.2026)
SENT_CHANNELS = {
    "ozon_chat": "Чат Ozon",
    "outside": "Вне Ozon: соцсети, мессенджеры",
    "review": "Ответ на отзыв",
    "other": "Другое",
}

CREATED, SENT, UNSENT, DELETED, RESTORED = "created", "sent", "unsent", "deleted", "restored"


class ReferralError(ValueError):
    """Текст — для пользователя, на русском."""


def new_code(taken: set[str]) -> str:
    """Короткий код участника, которого ещё нет в магазине."""
    for _ in range(1000):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in taken:
            return code
    raise ReferralError("Не удалось придумать новый код участника — попробуйте ещё раз")  # 31⁶ ≈ 887 млн, не бывает


def content_of(code: str) -> str:
    return f"{CONTENT_PREFIX}{code}"


def code_of(content: str | None) -> str | None:
    """utm_content из отчёта Ozon → код участника (`ref_ab3k7m` → `ab3k7m`); не наш — None."""
    m = re.fullmatch(r"ref_([a-z2-9]{4,16})", (content or "").strip().lower())
    return m.group(1) if m else None


@dataclass(frozen=True)
class Program:
    campaign_name: str = DEFAULT_CAMPAIGN
    utm_source: str = DEFAULT_SOURCE
    utm_medium: str = DEFAULT_MEDIUM
    store_url: str | None = None


def check_program(campaign: str | None, source: str | None, medium: str | None, store_url: str | None) -> Program:
    """Настройки программы: название кампании — как в конструкторе (латиница, цифры, «_», «-»), метки — непустые, ссылка
    на магазин — ссылка Ozon на раздел магазина."""
    name = tl.check_campaign(campaign)
    src = (source or "").strip()
    med = (medium or "").strip()
    if not src or not med:
        raise ReferralError("Укажите utm_source и utm_medium — по ним ссылки программы видны в отчётах Ozon отдельно")
    url = None
    if (store_url or "").strip():
        link = tl.parse_link(store_url or "")
        if link.section != "seller":
            raise ReferralError("Ссылка на магазин — страница магазина в Ozon, например https://www.ozon.ru/seller/имя/")
        url = tl.plain_url(link)
    return Program(name, src[:200], med[:200], url)


def labels(prefix: str | None, program: Program, code: str) -> tl.Labels:
    """Метки личной ссылки: общие у программы + код участника в utm_content."""
    return tl.make_labels(prefix=prefix, campaign=program.campaign_name, channel="other", source=program.utm_source,
                          medium=program.utm_medium, content=content_of(code))


def check_name(name: str | None) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise ReferralError("Укажите имя — как подписан отзыв, чтобы потом узнать человека")
    if len(n) > MAX_NAME:
        raise ReferralError(f"Имя — не длиннее {MAX_NAME} знаков")
    return n


def sent_state(kinds: list[tuple[str, object]]) -> tuple[bool, object | None]:
    """События участника по времени (вид, событие) → отправлена ли ссылка сейчас и каким событием (последнее sent)."""
    last = None
    for kind, ev in kinds:
        if kind == SENT:
            last = ev
        elif kind == UNSENT:
            last = None
    return last is not None, last
