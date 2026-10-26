"""Уведомления (шаги 7.3, 10.25): разделы, каналы, что присылать, тихие часы, сводка, текст сообщения. Чистый модуль —
без БД и HTTP.

Разделы — по карте сервиса (SITEMAP п. 9), каждая аномалия ровно в одном: продажи; низкая маржа (и убыток в акции);
остатки; кассовый разрыв (и операции без статьи); выплаты Ozon и банк; автодобавление в акцию; изменения цен и
комиссии; синхронизация и отчёты (10.5); автоматизация — с шагов 9.x. У раздела (`SectionPrefs`):
- `on` — уведомлять: выключенный раздел не тревожит нигде (колокольчик, «Требует внимания», Telegram); аномалии
  проверяются и хранятся как раньше и видны на странице «Уведомления» с пометкой «раздел выключен»;
- каналы: в сервисе — всегда (пока раздел включён), `telegram`, e-mail — после почты (8.3a);
- `severity` — что слать в Telegram: all — красные и жёлтые, red — только красные (у раздела с одной важностью не
  действует);
- `daily` — в Telegram сразу или сводкой раз в день (в `digest_at` по поясу пользователя).
До 10.25 Telegram настраивался категориями (красные, жёлтые, автоакции, деньги, данные) — `legacy_sections` переводит их
в разделы без потери выбора: настройки, которые ещё не сохраняли по разделам, читаются так.
Тихие часы — в часовом поясе пользователя (пояса России без перехода на летнее время — смещение от UTC постоянное):
сообщения не уходят, а копятся и уходят одним сообщением, когда тихие часы кончатся; сводка, попавшая в тихие часы, —
тоже после них.
"""
import html
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from app.engine import anomaly_detector as det
from app.engine.anomaly_text import Text

ALL, RED_ONLY = "all", "red"

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
class Section:
    key: str
    title: str
    hint: str  # что в разделе — для страницы и настроек (правило 15)
    types: tuple[str, ...]
    severities: tuple[str, ...] = ("red", "yellow")  # какие бывают: одна — выбор важности не нужен
    soon: str | None = None  # раздела ещё нет — с какого шага


SECTIONS: tuple[Section, ...] = (
    Section("sales", "Продажи", "Продажи магазина или товара упали или резко выросли — 3 дня против 7 предыдущих.",
            (det.SALES_DROP, det.SALES_GROWTH)),
    Section("margin", "Низкая маржа", "Маржа при текущей цене ниже вашего порога или ниже нуля; убыток в акции.",
            (det.LOW_MARGIN, det.PROMO_LOSS), ("red",)),
    Section("stock", "Остатки", "Запаса меньше порога «Мало» или «Критично» — пора везти; больше порога «Избыток».",
            (det.LOW_STOCK, det.EXCESS_STOCK)),
    Section("cash", "Кассовый разрыв", "С «Планированием»: прогноз остатка на счетах ниже нуля или вашего порога; "
            "операции без статьи.", (det.CASH_GAP, det.UNCATEGORIZED)),
    Section("payouts", "Выплаты Ozon и банк", "Выплата Ozon не пришла или разошлась с прогнозом; не дошла до счёта или "
            "пришла другой суммой.", (det.PAYOUT_MISSED, det.PAYOUT_DIFF, det.BANK_MISSING, det.BANK_DIFF)),
    Section("auto_promo", "Автодобавление в акцию", "Ozon сам добавил товар в акцию — успейте убрать, если она невыгодна.",
            (det.PROMO_AUTO_ADDED,), ("yellow",)),
    Section("prices", "Изменения цен", "Цена или комиссия Ozon заметно изменились за 7 дней — через сервис или нет.",
            (det.PRICE_CHANGE, det.COMMISSION_CHANGE)),
    Section("data", "Синхронизация и отчёты", "Ключ Ozon или токен банка не принят, данные давно не обновлялись, "
            "пропуски в начислениях, отчёт с ошибкой, продажи без себестоимости.",
            (det.SYNC_PROBLEM, det.ACCRUAL_GAPS, det.REPORT_ERROR, det.MISSING_COST)),
    Section("automation", "Автоматизация", "Что сделали автоматические правила цен, акций и поставок и где они "
            "остановились.", (), soon="9.x"),
)
SECTION_KEYS = tuple(x.key for x in SECTIONS)
BY_KEY = {x.key: x for x in SECTIONS}
SECTION_OF = {t: x.key for x in SECTIONS for t in x.types}
assert set(SECTION_OF) == set(det.TYPES), "каждая аномалия — ровно в одном разделе"


def section_of(type_: str) -> str | None:
    return SECTION_OF.get(type_)


def types_of(keys) -> list[str]:
    return [t for k in keys for t in BY_KEY[k].types]


@dataclass(frozen=True)
class SectionPrefs:
    on: bool = True  # уведомлять: колокольчик, «Требует внимания», Telegram
    telegram: bool = True
    severity: str = ALL  # all | red — что слать в Telegram
    daily: bool = False  # в Telegram сводкой раз в день, а не сразу

    def as_dict(self) -> dict:
        return {"on": self.on, "telegram": self.telegram, "severity": self.severity, "daily": self.daily}


def legacy_sections(red: bool = True, yellow: bool = False, auto_promo: bool = True, money: bool = True,
                    data: bool = False) -> dict[str, SectionPrefs]:
    """Категории Telegram до 10.25 → разделы: «Деньги» — кассовый разрыв и выплаты, любая важность; «Автоакции» и
    «Данные» — свои разделы; остальное — по важности: красные и (или) жёлтые."""
    by_severity = SectionPrefs(telegram=red or yellow, severity=ALL if yellow else RED_ONLY)
    out = {k: by_severity for k in ("sales", "margin", "stock", "prices")}
    out.update(cash=SectionPrefs(telegram=money), payouts=SectionPrefs(telegram=money),
               auto_promo=SectionPrefs(telegram=auto_promo), data=SectionPrefs(telegram=data),
               automation=SectionPrefs())
    return out


DEFAULT_SECTIONS = legacy_sections()


def parse_sections(raw: dict | None, legacy: dict[str, SectionPrefs] | None = None) -> dict[str, SectionPrefs]:
    """Сохранённые разделы (JSON); раздела там нет — как перенесено из старых категорий (или по умолчанию)."""
    base = legacy or DEFAULT_SECTIONS
    out = {}
    for key in SECTION_KEYS:
        r = (raw or {}).get(key)
        if not isinstance(r, dict):
            out[key] = base[key]
            continue
        sev = r.get("severity")
        out[key] = SectionPrefs(bool(r.get("on", base[key].on)), bool(r.get("telegram", base[key].telegram)),
                                sev if sev in (ALL, RED_ONLY) else base[key].severity, bool(r.get("daily", base[key].daily)))
    return out


@dataclass(frozen=True)
class Prefs:
    sections: dict[str, SectionPrefs] = field(default_factory=lambda: dict(DEFAULT_SECTIONS))
    quiet_enabled: bool = False
    quiet_from: int = 23 * 60  # минуты от полуночи, местное время
    quiet_to: int = 8 * 60
    timezone: str = "Europe/Moscow"
    digest_at: int = 9 * 60  # сводка раз в день — во сколько, местное время

    def section(self, key: str | None) -> SectionPrefs:
        return self.sections.get(key or "", SectionPrefs(on=False, telegram=False))

    def enabled(self) -> list[str]:
        """Какие разделы присылаем в Telegram — подписи по порядку, «сводкой» — с пометкой."""
        out = []
        for x in SECTIONS:
            sp = self.section(x.key)
            if x.soon or not (sp.on and sp.telegram):
                continue
            mods = [m for m in ("только красные" if sp.severity == RED_ONLY and len(x.severities) > 1 else None,
                                "сводкой" if sp.daily else None) if m]
            out.append(x.title[:1].lower() + x.title[1:] + (f" ({', '.join(mods)})" if mods else ""))
        return out

    def muted_types(self) -> set[str]:
        """Аномалии выключенных разделов — не тревожат на сайте (колокольчик, «Требует внимания»)."""
        return {t for x in SECTIONS if not self.section(x.key).on for t in x.types}


def shown(prefs: Prefs, type_: str) -> bool:
    """В сервисе (колокольчик, «Требует внимания») — пока раздел включён."""
    return prefs.section(section_of(type_)).on


def wanted(prefs: Prefs, type_: str, severity: str) -> bool:
    """В Telegram: раздел включён, Telegram у него включён, важность подходит."""
    key = section_of(type_)
    sp = prefs.section(key)
    if not (sp.on and sp.telegram):
        return False
    single = key is not None and len(BY_KEY[key].severities) == 1
    return single or sp.severity == ALL or severity == "red"


def daily(prefs: Prefs, type_: str) -> bool:
    return prefs.section(section_of(type_)).daily


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


def next_digest(prefs: Prefs, now: datetime) -> datetime:
    """Когда уйдёт сводка раз в день: ближайшее `digest_at` по поясу пользователя (сегодня, если ещё не наступило, иначе
    завтра); попало в тихие часы — после них. UTC."""
    zone = tz(prefs.timezone)
    local = now.astimezone(zone)
    at = datetime.combine(local.date(), time(prefs.digest_at // 60, prefs.digest_at % 60), zone)
    if at <= local:
        at += timedelta(days=1)
    at = at.astimezone(timezone.utc)
    return quiet_until(prefs, at) or at


def send_after(prefs: Prefs, type_: str, now: datetime) -> datetime:
    """Когда отправить уведомление в Telegram: сводкой — в ближайшую сводку; сразу — сейчас или после тихих часов."""
    if daily(prefs, type_):
        return next_digest(prefs, now)
    return quiet_until(prefs, now) or now


@dataclass(frozen=True)
class Item:
    """Аномалия для сообщения: тексты — engine/anomaly_text.describe."""

    severity: str
    text: Text
    product: str | None = None  # «артикул · название»
    store: str | None = None  # название магазина — у аккаунта с несколькими магазинами (шаг 8.4b)
    store_id: str | None = None  # ссылка откроет сайт в этом магазине (?store=…)
    section: str | None = None  # раздел (10.25)


def _url(site: str, link: str | None, store_id: str | None = None) -> str:
    url = f"{site.rstrip('/')}{link or '/dashboard/alerts'}"
    if store_id:
        path, _, anchor = url.partition("#")
        url = f"{path}{'&' if '?' in path else '?'}store={store_id}" + (f"#{anchor}" if anchor else "")
    return url


def render_one(item: Item, site: str) -> str:
    """Одна аномалия: важность, заголовок, раздел и магазин, товар, пояснение, ссылка на сайт. HTML-разметка Telegram."""
    e = html.escape
    lines = [f"{SEVERITY_MARK.get(item.severity, '🔔')} <b>{e(item.text.title)}</b>"]
    where = [f"Раздел: {BY_KEY[item.section].title}" if item.section in BY_KEY else None,
             f"Магазин: {item.store}" if item.store else None]
    if any(where):
        lines.append(e(" · ".join(w for w in where if w)))
    if item.product:
        lines.append(e(item.product))
    if item.text.message:
        lines += ["", e(item.text.message)]
    lines += ["", f'<a href="{e(_url(site, item.text.link, item.store_id))}">Открыть в Ozon Analytics →</a>']
    return "\n".join(lines)


def render_digest(items: list[Item], site: str, daily_digest: bool = False) -> str:
    """Несколько аномалий одним сообщением (после тихих часов или сводка раз в день): по разделам в порядке карты,
    внутри — красные первыми, у каждой — ссылка."""
    e = html.escape
    order = {k: i for i, k in enumerate(SECTION_KEYS)}
    ordered = sorted(items, key=lambda i: (order.get(i.section or "", len(order)), i.severity != "red"))
    head = "Сводка за день" if daily_digest else "Новые уведомления"
    lines = [f"🔔 <b>{head}: {len(items)}</b>"]
    current = None
    for it in ordered[:MAX_DIGEST]:
        if it.section != current:
            current = it.section
            lines += ["", f"<b>{e(BY_KEY[current].title if current in BY_KEY else 'Другое')}</b>"]
        product = f" — {e(it.product)}" if it.product else ""
        store = f"{e(it.store)}: " if it.store else ""
        url = e(_url(site, it.text.link, it.store_id))
        lines.append(f'{SEVERITY_MARK.get(it.severity, "•")} {store}<a href="{url}">{e(it.text.title)}</a>{product}')
    if len(items) > MAX_DIGEST:
        lines += ["", f"и ещё {len(items) - MAX_DIGEST} — на сайте"]
    lines += ["", f'<a href="{e(_url(site, "/dashboard/alerts"))}">Все уведомления →</a>']
    return "\n".join(lines)


def render_test(site: str, prefs: Prefs) -> str:
    on = prefs.enabled()
    quiet = (f"Тихие часы: {hhmm(prefs.quiet_from)}–{hhmm(prefs.quiet_to)}." if prefs.quiet_enabled
             else "Тихие часы выключены.")
    digest = (f"Сводка раз в день — в {hhmm(prefs.digest_at)}."
              if any(sp.on and sp.telegram and sp.daily for sp in prefs.sections.values()) else None)
    return "\n".join([
        "✅ <b>Тестовое сообщение Ozon Analytics</b>",
        "",
        "Уведомления будут приходить сюда. Присылаем: " + (", ".join(on) if on else "ничего — всё выключено") + ".",
        *([digest] if digest else []),
        quiet,
        "",
        f'<a href="{html.escape(_url(site, "/dashboard/settings/notifications"))}">Настройки уведомлений →</a>',
    ])
