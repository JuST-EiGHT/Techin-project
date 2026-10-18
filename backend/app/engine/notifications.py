"""Уведомления в Telegram (шаг 7.3): что присылать, тихие часы, текст сообщения. Чистый модуль — без БД и HTTP.

Категории не пересекаются — каждая аномалия ровно в одной:
- «Деньги» — проверки денег (выплаты Ozon, сверка с банком, кассовый разрыв, операции без статьи), любая важность;
- «Автоакции» — Ozon сам добавил товар в акцию;
- остальное — по важности: «Красные», «Жёлтые».
Тихие часы — в часовом поясе пользователя (пояса России без перехода на летнее время — смещение от UTC постоянное):
сообщения не уходят, а копятся и уходят одним сообщением, когда тихие часы кончатся.
"""
import html
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from app.engine import anomaly_detector as det
from app.engine.anomaly_text import Text

RED, YELLOW, AUTO_PROMO, MONEY = "red", "yellow", "auto_promo", "money"
CATEGORY_TITLES = {RED: "красные", YELLOW: "жёлтые", AUTO_PROMO: "автоакции", MONEY: "деньги"}

# Часовые пояса России: IANA-имя, подпись, смещение от UTC (часы; переходов на летнее время нет с 2014 года)
TIMEZONES: list[tuple[str, str, int]] = [
    ("Europe/Kaliningrad", "Калининград", 2), ("Europe/Moscow", "Москва", 3), ("Europe/Samara", "Самара", 4),
    ("Asia/Yekaterinburg", "Екатеринбург", 5), ("Asia/Omsk", "Омск", 6), ("Asia/Novosibirsk", "Новосибирск", 7),
    ("Asia/Krasnoyarsk", "Красноярск", 7), ("Asia/Irkutsk", "Иркутск", 8), ("Asia/Yakutsk", "Якутск", 9),
    ("Asia/Vladivostok", "Владивосток", 10), ("Asia/Magadan", "Магадан", 11), ("Asia/Kamchatka", "Камчатка", 12),
]
OFFSETS = {name: hours for name, _, hours in TIMEZONES}
MAX_DIGEST = 10  # больше — «и ещё N — на сайте» (лимит сообщения Telegram — 4096 знаков)
SEVERITY_MARK = {"red": "🔴", "yellow": "🟡"}


@dataclass(frozen=True)
class Prefs:
    red: bool = True
    yellow: bool = False
    auto_promo: bool = True
    money: bool = True
    quiet_enabled: bool = False
    quiet_from: int = 23 * 60  # минуты от полуночи, местное время
    quiet_to: int = 8 * 60
    timezone: str = "Europe/Moscow"


def category(type_: str, severity: str) -> str:
    if det.GROUPS.get(type_) == "money":
        return MONEY
    if type_ == det.PROMO_AUTO_ADDED:
        return AUTO_PROMO
    return RED if severity == "red" else YELLOW


def wanted(prefs: Prefs, type_: str, severity: str) -> bool:
    return {RED: prefs.red, YELLOW: prefs.yellow, AUTO_PROMO: prefs.auto_promo, MONEY: prefs.money}[category(type_, severity)]


def tz(name: str) -> timezone:
    return timezone(timedelta(hours=OFFSETS.get(name, 3)))


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def quiet_until(prefs: Prefs, now: datetime) -> datetime | None:
    """Сейчас тихие часы — когда они кончатся (UTC); иначе None. Интервал [с, до), может переходить через полночь."""
    if not prefs.quiet_enabled or prefs.quiet_from == prefs.quiet_to:
        return None
    zone = tz(prefs.timezone)
    local = now.astimezone(zone)
    minute = local.hour * 60 + local.minute
    start, end = prefs.quiet_from, prefs.quiet_to
    inside = start <= minute < end if start < end else (minute >= start or minute < end)
    if not inside:
        return None
    end_day = local.date() if minute < end else local.date() + timedelta(days=1)
    return datetime.combine(end_day, time(end // 60, end % 60), zone).astimezone(timezone.utc)


@dataclass(frozen=True)
class Item:
    """Аномалия для сообщения: тексты — engine/anomaly_text.describe."""

    severity: str
    text: Text
    product: str | None = None  # «артикул · название»


def _url(site: str, link: str | None) -> str:
    return f"{site.rstrip('/')}{link or '/dashboard/alerts'}"


def render_one(item: Item, site: str) -> str:
    """Одна аномалия: важность, заголовок, товар, пояснение, ссылка на сайт. HTML-разметка Telegram."""
    e = html.escape
    lines = [f"{SEVERITY_MARK.get(item.severity, '🔔')} <b>{e(item.text.title)}</b>"]
    if item.product:
        lines.append(e(item.product))
    if item.text.message:
        lines += ["", e(item.text.message)]
    lines += ["", f'<a href="{e(_url(site, item.text.link))}">Открыть в Ozon Analytics →</a>']
    return "\n".join(lines)


def render_digest(items: list[Item], site: str) -> str:
    """Несколько аномалий одним сообщением (после тихих часов): красные первыми, у каждой — ссылка."""
    e = html.escape
    ordered = sorted(items, key=lambda i: i.severity != "red")
    lines = [f"🔔 <b>Новые уведомления: {len(items)}</b>", ""]
    for it in ordered[:MAX_DIGEST]:
        product = f" — {e(it.product)}" if it.product else ""
        lines.append(f'{SEVERITY_MARK.get(it.severity, "•")} <a href="{e(_url(site, it.text.link))}">{e(it.text.title)}</a>{product}')
    if len(items) > MAX_DIGEST:
        lines += ["", f"и ещё {len(items) - MAX_DIGEST} — на сайте"]
    lines += ["", f'<a href="{e(_url(site, "/dashboard/alerts"))}">Все уведомления →</a>']
    return "\n".join(lines)


def render_test(site: str, prefs: Prefs) -> str:
    on = [CATEGORY_TITLES[c] for c, flag in ((RED, prefs.red), (YELLOW, prefs.yellow), (AUTO_PROMO, prefs.auto_promo),
                                                 (MONEY, prefs.money)) if flag]
    quiet = (f"Тихие часы: {hhmm(prefs.quiet_from)}–{hhmm(prefs.quiet_to)}." if prefs.quiet_enabled
             else "Тихие часы выключены.")
    return "\n".join([
        "✅ <b>Тестовое сообщение Ozon Analytics</b>",
        "",
        "Уведомления будут приходить сюда. Присылаем: " + (", ".join(on) if on else "ничего — всё выключено") + ".",
        quiet,
        "",
        f'<a href="{html.escape(_url(site, "/dashboard/settings/notifications"))}">Настройки уведомлений →</a>',
    ])
