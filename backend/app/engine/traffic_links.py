"""Ссылки «Внешнего трафика» Ozon с UTM-метками (шаг 11.1). Чистый модуль: без БД и HTTP.

У Ozon нет API для внешнего трафика: ссылку продавец размечает сам и ставит во внешнюю рекламу, Ozon считает переходы и
заказы по меткам. Правила Ozon (база знаний, «Внешний трафик»):
- кампанию Ozon узнаёт только по `utm_campaign` = префикс продавца `vendor_org_<ID организации>` (+ «_название»); без
  префикса переходы не попадут в статистику продавца;
- размечать можно только ссылки на ozon.ru / ozon.kz / ozon.by и только на товар (`/product/`), магазин
  (`/seller/…`, в базе знаний — `/product/seller/`), бренд (`/brand/`), акцию или подборку (`/highlight/`);
- остальные метки (source, medium, term, content) — обычные: без source и medium в отчёте не будет разбивки по источникам.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

MAX_LINKS = 100  # как в конструкторе Ozon: до 100 ссылок за раз
MAX_LABEL = 200
OZON_CABINET_URL = "https://seller.ozon.ru/app/advertisement/vendor-traffic"
HOSTS = ("ozon.ru", "ozon.kz", "ozon.by")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")

_PREFIX = re.compile(r"^vendor_org_\d{1,20}$")
_CAMPAIGN = re.compile(r"^[a-z0-9_-]{1,64}$")
_URL_LIKE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class LinkError(ValueError):
    """Текст — для пользователя, на русском: что не так и что сделать."""


# ---------- разделы Ozon ----------

SECTIONS = {
    "product": "Товар",
    "seller": "Магазин",
    "brand": "Бренд",
    "highlight": "Акция или подборка",
}


def section_of(path: str) -> str | None:
    """Раздел Ozon по пути ссылки. Магазин — `/seller/<имя>/` (так страница магазина открывается в браузере, ссылка
    владельца 18.11.2026) и `/product/seller/…` (как в базе знаний Ozon); проверяется раньше товара — `/product/seller/`
    начинается с `/product/`."""
    p = "/" + path.lstrip("/").lower()
    for prefix, kind in (("/product/seller/", "seller"), ("/seller/", "seller"), ("/product/", "product"),
                         ("/brand/", "brand"), ("/highlight/", "highlight")):
        if p.startswith(prefix):
            return kind if len(p.rstrip("/")) > len(prefix.rstrip("/")) else None
    return None


# служебные параметры кнопки «Поделиться» Ozon (у каждого, кто поделился, свои) — в ссылку с метками не переносим
SHARE_PARAMS = {"at", "sh", "_bctx", "hs"}


@dataclass(frozen=True)
class OzonLink:
    base: str  # https://www.ozon.ru/product/…/ — без меток, свои параметры ссылки сохранены
    section: str
    params: tuple[tuple[str, str], ...]  # параметры ссылки, кроме utm_*


def parse_link(text: str) -> OzonLink:
    """Ссылка из строки пользователя → проверенная ссылка Ozon. Ошибка — LinkError с понятным текстом."""
    raw = (text or "").strip()
    if not raw:
        raise LinkError("Пустая строка")
    if any(c.isspace() for c in raw):
        raise LinkError("В ссылке есть пробел — по одной ссылке в строке")
    if not _URL_LIKE.match(raw):
        raw = "https://" + raw  # «ozon.ru/product/…» без https://
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise LinkError("Не похоже на ссылку — скопируйте её из адресной строки браузера") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise LinkError("Ссылка должна начинаться с https://")
    host = (parts.hostname or "").lower().rstrip(".")
    bare = host[4:] if host.startswith("www.") else host
    if bare not in HOSTS or port not in (None, 443, 80) or parts.username or parts.password:
        raise LinkError("Ozon считает только ссылки на ozon.ru, ozon.kz или ozon.by")
    section = section_of(parts.path)
    if section is None:
        raise LinkError(
            "Размечать можно только ссылки на товар (/product/), магазин (/seller/), бренд (/brand/) "
            "или акцию и подборку (/highlight/)"
        )
    params = tuple(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in SHARE_PARAMS
    )
    base = urlunsplit(("https", f"www.{bare}", parts.path or "/", "", ""))
    return OzonLink(base=base, section=section, params=params)


def product_url(sku: int, host: str = "ozon.ru") -> str:
    """Ссылка на товар по SKU Ozon: страница товара открывается и без названия в адресе."""
    return f"https://www.{host}/product/{sku}/"


# ---------- метки ----------


def check_prefix(text: str | None) -> str:
    """Префикс для метки UTM_CAMPAIGN из кабинета Ozon: `vendor_org_<цифры>`; пробелы, регистр и «_» в конце — прощаем."""
    p = (text or "").strip().lower().rstrip("_")
    if not p:
        raise LinkError(
            "Укажите префикс для метки UTM_CAMPAIGN — без него Ozon не засчитает ни одного перехода. "
            f"Он в кабинете Ozon: Продвижение → Внешний трафик ({OZON_CABINET_URL})"
        )
    if _PREFIX.match(p):
        return p
    m = re.match(r"^(vendor_org_\d+)_", p)
    if m:
        raise LinkError(f"Это уже метка кампании целиком. Префикс — только «{m.group(1)}», название кампании — отдельно")
    raise LinkError("Префикс выглядит так: vendor_org_ и цифры, например vendor_org_123456 — скопируйте его из кабинета Ozon")


def check_campaign(name: str | None) -> str:
    """Название кампании — часть utm_campaign после префикса: латиница, цифры, «_» и «-»."""
    n = (name or "").strip().lower()
    if not n:
        raise LinkError("Укажите название кампании — по нему вы найдёте её в отчётах Ozon")
    n = re.sub(r"\s+", "_", n)
    if not _CAMPAIGN.match(n):
        raise LinkError("Название кампании: латинские буквы, цифры, «_» и «-», до 64 знаков — например vk_october")
    return n


def campaign_value(prefix: str, name: str) -> str:
    return f"{check_prefix(prefix)}_{check_campaign(name)}"


def _label(value: str | None, title: str) -> str:
    v = (value or "").strip()
    if len(v) > MAX_LABEL:
        raise LinkError(f"{title}: не длиннее {MAX_LABEL} знаков")
    if any(c in v for c in "\r\n\t"):
        raise LinkError(f"{title}: без переносов строк")
    return v


# ---------- каналы ----------


@dataclass(frozen=True)
class Macro:
    code: str  # как его подставляет рекламная сеть
    label: str


@dataclass(frozen=True)
class Channel:
    key: str
    label: str
    source: str  # utm_source: у рекламных сетей — сам, у остальных — подсказка
    medium: str
    fixed_source: bool  # рекламная сеть: utm_source задаётся сам
    hint: str
    macros: tuple[Macro, ...] = field(default=())
    # подсказки значений в выпадающем списке (как у Ozon); можно вписать своё
    source_options: tuple[str, ...] = field(default=())
    medium_options: tuple[str, ...] = field(default=())


_MT_MACROS = (
    Macro("{{campaign_id}}", "ID кампании"),
    Macro("{{banner_id}}", "ID объявления"),
    Macro("{{geo}}", "Регион"),
    Macro("{{gender}}", "Пол"),
    Macro("{{age}}", "Возраст"),
)

# каналы — как в конструкторе Ozon; у рекламных сетей utm_source задаётся сам, макросы сеть подставит при показе
_AD_MEDIUM = ("cpc", "cpm", "cpa")

CHANNELS: dict[str, Channel] = {c.key: c for c in (
    Channel("yandex", "Яндекс (Директ, РСЯ)", "yandex", "cpc", True,
            "Поиск и Рекламная сеть Яндекса",
            (Macro("{source_type}", "Тип площадки: поиск или сеть"), Macro("{source}", "Площадка"),
             Macro("{region_name}", "Регион"), Macro("{device_type}", "Устройство"),
             Macro("{campaign_id}", "ID кампании"), Macro("{ad_id}", "ID объявления"), Macro("{keyword}", "Ключевая фраза")),
            ("yandex",), _AD_MEDIUM),
    Channel("mytarget", "myTarget", "mytarget", "cpc", True, "Рекламная платформа myTarget", _MT_MACROS,
            ("mytarget",), _AD_MEDIUM),
    Channel("vk", "VK Реклама", "vk", "cpc", True, "Рекламный кабинет VK", _MT_MACROS, ("vk",), _AD_MEDIUM),
    Channel("social", "Социальные сети и платформы", "", "social", False,
            "Блогеры, свои страницы и сообщества: utm_source — сеть (inst, tg, vk), utm_medium — где ссылка",
            (), ("inst", "vk", "tg", "youtube", "dzen", "ok", "tiktok", "pinterest", "rutube"),
            ("social", "description", "post", "story", "reels", "video", "comment", "blogger")),
    Channel("other", "Другое", "", "", False, "Сайты, рассылки, QR-коды: utm_source и utm_medium — свои",
            (), ("site", "email", "sms", "qr", "partner", "blog", "forum", "offline"),
            ("referral", "email", "banner", "qr", "offline", "article", "cpc")),
)}


@dataclass(frozen=True)
class Labels:
    campaign: str  # полное значение utm_campaign с префиксом
    source: str = ""
    medium: str = ""
    term: str = ""
    content: str = ""


def make_labels(
    *, prefix: str | None, campaign: str | None, channel: str, source: str | None = None, medium: str | None = None,
    term: str | None = None, content: str | None = None,
) -> Labels:
    """Метки ссылки. utm_source и utm_medium обязательны, как в конструкторе Ozon (решение владельца 17.11.2026): без них
    в отчёте Ozon нет разбивки по источникам."""
    ch = CHANNELS.get(channel)
    if ch is None:
        raise LinkError("Выберите канал")
    utm_campaign = campaign_value(prefix or "", campaign or "")  # сначала префикс и кампания — без них ничего не засчитается
    src = ch.source if ch.fixed_source else _label(source, "utm_source")
    med = _label(medium if medium is not None else ch.medium, "utm_medium")
    if not src:
        raise LinkError("Укажите utm_source — откуда переход: inst, vk, tg, site… "
                        "Без него в отчёте Ozon не будет разбивки по источникам")
    if not med:
        raise LinkError("Укажите utm_medium — какой это трафик или где ссылка: post, story, description… "
                        "Без него в отчёте Ozon не будет разбивки по источникам")
    return Labels(
        campaign=utm_campaign,
        source=src,
        medium=med,
        term=_label(term, "utm_term"),
        content=_label(content, "utm_content"),
    )


_SAFE = "{}_-.~"  # фигурные скобки — не кодируем: рекламная сеть подставит макрос


def tagged_url(link: OzonLink, labels: Labels) -> str:
    """Ссылка с метками: свои параметры ссылки — как были, прежние utm_* — заменены нашими, пустые метки не пишем."""
    pairs = list(link.params) + [
        (k, v) for k, v in (
            ("utm_source", labels.source), ("utm_medium", labels.medium), ("utm_campaign", labels.campaign),
            ("utm_term", labels.term), ("utm_content", labels.content),
        ) if v
    ]
    query = "&".join(f"{quote(k, safe=_SAFE)}={quote(v, safe=_SAFE)}" for k, v in pairs)
    return f"{link.base}?{query}"


# ---------- список ссылок ----------


@dataclass(frozen=True)
class BuildRow:
    line: int  # № строки ввода (с 1)
    input: str  # что ввёл пользователь (ссылка или «артикул — название»)
    url: str | None = None
    section: str | None = None
    error: str | None = None
    product_id: str | None = None


def split_lines(text: str | None) -> list[tuple[int, str]]:
    """Список ссылок «по одной в строке»: пустые строки пропускаем, номер — как в поле ввода."""
    return [(i, s.strip()) for i, s in enumerate((text or "").splitlines(), start=1) if s.strip()]


def build_rows(items: list[tuple[int, str, str | None]], labels: Labels) -> list[BuildRow]:
    """items — (№ по порядку, ссылка, id товара или None). Повтор той же ссылки — ошибка у повтора, первая собирается."""
    if len(items) > MAX_LINKS:
        raise LinkError(f"Не больше {MAX_LINKS} ссылок за раз — как в конструкторе Ozon; сейчас {len(items)}")
    seen: dict[str, int] = {}
    rows: list[BuildRow] = []
    for line, text, product_id in items:
        try:
            link = parse_link(text)
        except LinkError as exc:
            rows.append(BuildRow(line, text, error=str(exc), product_id=product_id))
            continue
        key = link.base + "?" + "&".join(f"{k}={v}" for k, v in link.params)
        if key in seen:
            rows.append(BuildRow(line, text, error="Повтор: эта ссылка уже есть в списке выше", product_id=product_id))
            continue
        seen[key] = line
        rows.append(BuildRow(line, text, url=tagged_url(link, labels), section=link.section, product_id=product_id))
    return rows
