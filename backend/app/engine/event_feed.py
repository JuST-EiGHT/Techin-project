"""Лента «История изменений и событий» (шаг 10.11). Чистый модуль — без БД и HTTP: событие, тексты, понятные названия
действий из журнала запросов, курсор и сборка страницы из нескольких источников.

Лента производная (правило 12): каждое событие собирается из уже хранимой записи — изменения цены, снимка цены, участия
в акции, снимка остатка, аномалии, журнала действий, запуска синхронизации, отчёта. Новых таблиц нет.

Страница — по времени назад от курсора: каждый источник отдаёт свои последние события до курсора (не больше K), общий
порядок — время по убыванию, при равном времени — ключ события. Если источник отдал K событий, хотя бы одно из них не
новее последнего события страницы (K ≥ размера страницы), значит, всё, что новее, уже собрано — страница верная.
"""
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote, unquote

from app.engine.anomaly_text import plural, rub

# виды событий — фильтр «тип события»
PRICE, PROMO, STOCK, ANOMALY, ACTION, ERROR = "price", "promo", "stock", "anomaly", "action", "error"
KINDS = (PRICE, PROMO, STOCK, ANOMALY, ACTION, ERROR)
KIND_TITLES = {
    PRICE: "Цены", PROMO: "Акции", STOCK: "Остатки и поставки", ANOMALY: "Аномалии", ACTION: "Действия",
    ERROR: "Ошибки загрузки",
}
OK, BAD, WARN = "ok", "bad", "warn"


@dataclass(frozen=True)
class ProductRef:
    id: str | None
    offer_id: str
    name: str | None


@dataclass
class Event:
    key: str  # «источник:id» — уникален в ленте, второй ключ сортировки
    at: datetime  # UTC
    kind: str
    title: str
    detail: str | None = None
    tone: str | None = None  # ok | bad | warn — цвет метки
    product: ProductRef | None = None
    link: str | None = None
    link_label: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def order(self) -> tuple[datetime, str]:
        return (self.at, self.key)


def utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── курсор ──
def encode_cursor(at: datetime, key: str) -> str:
    return base64.urlsafe_b64encode(f"{utc(at).isoformat()}|{key}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    """Курсор → (время, ключ); испорчен — None."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, key = raw.split("|", 1)
        return utc(datetime.fromisoformat(at)), key
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None


@dataclass
class Page:
    events: list[Event]
    next: str | None  # курсор следующей (более старой) страницы; None — дальше нет


def page(sources: list[tuple[list[Event], bool]], limit: int, before: tuple[datetime, str] | None = None,
         since: datetime | None = None) -> Page:
    """Страница из событий источников: (события, отдал ли источник столько, сколько просили — значит, есть ещё).
    Строго раньше курсора и не раньше since; по времени по убыванию."""
    events = [e for evs, _ in sources for e in evs
              if (before is None or (utc(e.at), e.key) < (before[0], before[1]))
              and (since is None or utc(e.at) >= since)]
    events.sort(key=lambda e: (utc(e.at), e.key), reverse=True)
    out = events[:limit]
    more = len(events) > limit or any(full for _, full in sources)
    return Page(out, encode_cursor(out[-1].at, out[-1].key) if more and out else None)


# ── цены ──
WHO = {"manual_single": "вручную", "manual_bulk": "списком", "auto_rule": "правилом"}
FAILED_TEXT = {
    "failed": "ошибка запроса к Ozon — ничего не изменилось", "rejected": "Ozon отказал",
    "blocked": "не прошло проверки безопасности", "unknown": "Ozon не ответил — цена могла измениться",
    "queued": "отправляется в Ozon",
}


def price_text(offer: str, before: Decimal | None, after: Decimal | None, min_before: Decimal | None,
               min_after: Decimal | None) -> str:
    if after is None and min_after is not None:
        return f"Минимальная цена {offer}: {rub(min_before)} → {rub(min_after)}"
    return f"Цена {offer}: {rub(before)} → {rub(after)}"


def price_change(key: str, at: datetime, product: ProductRef, change_type: str, status: str, before, after,
                 min_before, min_after, rollback: bool, reason: str | None, error: str | None) -> Event:
    """Изменение цены через сервис (6.2–6.4): кто — вручную, списком, откат, правило; чем закончилось."""
    who = "откат" if rollback else WHO.get(change_type, change_type)
    title = price_text(product.offer_id, before, after, min_before, min_after)
    if status == "applied":
        detail, tone = f"через сервис, {who}", None
    else:
        title = f"{title} — не изменена" if status in ("failed", "rejected", "blocked") else title
        detail = f"через сервис, {who}: {FAILED_TEXT.get(status, status)}"
        tone = WARN if status in ("unknown", "queued") else BAD
        if error and status in ("failed", "rejected"):
            detail += f" ({error})"
    if reason and not rollback and change_type == "auto_rule":
        detail += f" — {reason}"
    return Event(key, at, PRICE, title, detail, tone, product, f"/dashboard/products/prices/history?product={product.id}",
                 "История цен")


def price_batch(key: str, at: datetime, total: int, applied: int) -> Event:
    """Цены списком (6.3) — одним событием на всё изменение."""
    goods = f"{total} {plural(total, 'товар', 'товара', 'товаров')}"
    not_applied = total - applied
    detail = "через сервис, списком: " + (f"применено у {applied}" if applied else "ни у одного не применилось")
    if applied and not_applied:
        detail += f", не изменено у {not_applied}"
    return Event(key, at, PRICE, f"Цены списком: {goods}", detail, BAD if not applied else WARN if not_applied else None,
                 None, "/dashboard/products/prices/history", "История цен")


def price_cabinet(key: str, at: datetime, product: ProductRef, before: Decimal, after: Decimal) -> Event:
    """Цена в снимке изменилась, а через сервис её не меняли."""
    arrow = "↑" if after > before else "↓"
    return Event(key, at, PRICE, f"Цена {product.offer_id}: {rub(before)} → {rub(after)} {arrow}",
                 "не через сервис — в кабинете Ozon или Ozon сам", None, product,
                 f"/dashboard/products/prices/history?product={product.id}", "История цен")


# ── акции ──
def promo_in(key: str, at: datetime, product: ProductRef, promo_id: str, title: str, auto: bool,
             action_price: Decimal | None) -> Event:
    detail = "Ozon добавил сам" if auto else "участвует"
    if action_price is not None:
        detail += f", цена в акции {rub(action_price)}"
    return Event(key, at, PROMO, f"{product.offer_id} вошёл в акцию «{title}»", detail, WARN if auto else None, product,
                 f"/dashboard/products/promotions/{promo_id}", "Акция")


def promo_out(key: str, at: datetime, product: ProductRef, promo_id: str, title: str) -> Event:
    return Event(key, at, PROMO, f"{product.offer_id} вышел из акции «{title}»", None, None, product,
                 f"/dashboard/products/promotions/{promo_id}", "Акция")


# ── остатки ──
def stockout(key: str, at: datetime, product: ProductRef) -> Event:
    return Event(key, at, STOCK, f"{product.offer_id} закончился на складах", "остаток на всех складах — 0", BAD, product,
                 f"/dashboard/products?product={product.id}", "Товар")


def restock(key: str, at: datetime, product: ProductRef, present: int) -> Event:
    return Event(key, at, STOCK, f"{product.offer_id} снова в наличии",
                 f"пришла поставка: на складах {present} шт.", OK, product, f"/dashboard/products?product={product.id}",
                 "Товар")


# ── аномалии ──
RESOLUTION = {"normal": "вернулось в норму", "gone": "больше не относится — товар в архиве или проверка выключена"}


def anomaly_found(key: str, at: datetime, product: ProductRef | None, type_title: str, title: str, red: bool,
                  link: str | None) -> Event:
    return Event(key, at, ANOMALY, title, f"{type_title} — {'красная' if red else 'жёлтая'}", BAD if red else WARN,
                 product, link, "Подробнее")


def anomaly_resolved(key: str, at: datetime, product: ProductRef | None, type_title: str, resolution: str | None,
                     link: str | None) -> Event:
    subject = f" — {product.offer_id}" if product else ""
    return Event(key, at, ANOMALY, f"Прошло: {type_title.lower()}{subject}", RESOLUTION.get(resolution or "", None),
                 OK, product, link, "Подробнее")


# ── ошибки загрузки ──
def sync_failed(key: str, at: datetime, kind_title: str, problem: str, hint: str, link: str, link_label: str) -> Event:
    return Event(key, at, ERROR, f"Не загружаются данные: {kind_title.lower()}", f"{problem}. {hint}", BAD, None, link,
                 link_label)


def report_error(key: str, at: datetime, report_id: str, name: str, message: str | None) -> Event:
    return Event(key, at, ERROR, f"Отчёт {name} не удалось перечитать", message or "итоги остались прежними", BAD, None,
                 f"/dashboard/analytics/reports/{report_id}", "Отчёт")


def report_uploaded(key: str, at: datetime, report_id: str, name: str, accrual: bool) -> Event:
    return Event(key, at, ACTION, f"Загружен отчёт {name}",
                 "отчёт по начислениям" if accrual else "отчёт о реализации", None, None,
                 f"/dashboard/analytics/reports/{report_id}", "Отчёт")


# ── действия пользователя (журнал запросов) ──
@dataclass(frozen=True)
class Action:
    title: str
    link: str | None = None
    label: str | None = None
    with_id: bool = False  # link + «/<id из пути>» (отчёт)


P = "/api/v1"
INTEGRATIONS = "/dashboard/settings/integrations"
# (метод, шаблон пути) → понятное действие. Каждый изменяющий эндпоинт — здесь или в IGNORED (тест следит за этим).
ACTIONS: dict[tuple[str, str], Action] = {
    ("PATCH", f"{P}/auth/me"): Action("Изменён профиль", "/dashboard/settings/account", "Профиль"),
    ("POST", f"{P}/auth/change-password"): Action("Сменён пароль", "/dashboard/settings/account", "Профиль"),
    ("POST", f"{P}/stores"): Action("Добавлен магазин", INTEGRATIONS, "Магазины"),
    ("PATCH", f"{P}/stores/{{store_id}}"): Action("Изменён магазин: название или отключение", INTEGRATIONS, "Магазины"),
    ("POST", f"{P}/credentials"): Action("Подключён ключ Ozon", INTEGRATIONS, "Интеграции"),
    ("DELETE", f"{P}/credentials/{{cred_id}}"): Action("Удалён ключ Ozon", INTEGRATIONS, "Интеграции"),
    ("POST", f"{P}/bank/connections"): Action("Подключён банк", f"{INTEGRATIONS}?tab=banks", "Банки"),
    ("DELETE", f"{P}/bank/connections/{{conn_id}}"): Action("Отключён банк", f"{INTEGRATIONS}?tab=banks", "Банки"),
    ("PUT", f"{P}/warehouses/withdrawal/params"): Action("Изменены параметры вывоза со складов",
                                                         "/dashboard/warehouses/withdrawal", "На вывоз"),
    ("PATCH", f"{P}/notifications/settings"): Action("Изменены настройки уведомлений", "/dashboard/settings/notifications",
                                                     "Уведомления"),
    ("POST", f"{P}/notifications/unlink"): Action("Telegram отвязан", "/dashboard/settings/notifications", "Уведомления"),
    ("PATCH", f"{P}/reports/{{report_id}}"): Action("Изменён отчёт: сумма «сколько пришло» или параметры расчёта",
                                                    "/dashboard/analytics/reports", "Отчёт", True),
    ("DELETE", f"{P}/reports/{{report_id}}"): Action("Отчёт скрыт", "/dashboard/analytics/reports", "Отчёт", True),
    ("POST", f"{P}/reports/{{report_id}}/restore"): Action("Отчёт восстановлен", "/dashboard/analytics/reports", "Отчёт",
                                                           True),
    ("POST", f"{P}/reports/{{report_id}}/reparse"): Action("Отчёт перечитан", "/dashboard/analytics/reports", "Отчёт",
                                                           True),
    ("PATCH", f"{P}/settings"): Action("Изменены настройки", "/dashboard/settings", "Настройки"),
    ("PUT", f"{P}/costs/{{offer_id:path}}"): Action("Изменена себестоимость", "/dashboard/products/costs", "Себестоимость"),
    ("POST", f"{P}/costs/import"): Action("Себестоимость загружена списком", "/dashboard/products/costs", "Себестоимость"),
    ("PUT", f"{P}/tax-profile"): Action("Изменён налоговый профиль", "/dashboard/settings/taxes", "Налоги"),
    ("POST", f"{P}/tax-profile/past-payments"): Action("Пересчитаны прошлые налоговые платежи", "/dashboard/planning/calendar",
                                                       "Календарь"),
    ("PUT", f"{P}/ozon/payout-settings"): Action("Изменён график выплат Ozon", "/dashboard/finance/payouts", "Выплаты"),
    ("PUT", f"{P}/planning/settings"): Action("Изменены настройки планирования", "/dashboard/planning", "Планирование"),
    ("POST", f"{P}/planning/setup"): Action("Настроено планирование", "/dashboard/planning", "Планирование"),
    ("POST", f"{P}/planning/accounts"): Action("Добавлен счёт", "/dashboard/operations/accounts", "Счета"),
    ("PATCH", f"{P}/planning/accounts/{{account_id}}"): Action("Изменён счёт", "/dashboard/operations/accounts", "Счета"),
    ("POST", f"{P}/planning/categories"): Action("Добавлена статья", "/dashboard/operations/categories", "Статьи"),
    ("POST", f"{P}/planning/categories/from-template"): Action("Статьи добавлены из шаблона",
                                                               "/dashboard/operations/categories", "Статьи"),
    ("PATCH", f"{P}/planning/categories/{{category_id}}"): Action("Изменена статья", "/dashboard/operations/categories",
                                                                  "Статьи"),
    ("POST", f"{P}/planning/operations"): Action("Добавлена операция", "/dashboard/operations", "Операции"),
    ("PATCH", f"{P}/planning/operations/{{op_id}}"): Action("Изменена операция", "/dashboard/operations", "Операции"),
    ("DELETE", f"{P}/planning/operations/{{op_id}}"): Action("Удалена операция", "/dashboard/operations", "Операции"),
    ("POST", f"{P}/planning/operations/import"): Action("Операции загружены списком", "/dashboard/operations", "Операции"),
    ("POST", f"{P}/planning/operations/categorize"): Action("Операциям назначена статья", "/dashboard/operations",
                                                            "Операции"),
    ("POST", f"{P}/planning/plans"): Action("Добавлен плановый платёж", "/dashboard/planning/calendar", "Календарь"),
    ("PATCH", f"{P}/planning/plans/{{plan_id}}"): Action("Изменён плановый платёж", "/dashboard/planning/calendar",
                                                         "Календарь"),
    ("DELETE", f"{P}/planning/plans/{{plan_id}}"): Action("Удалён плановый платёж", "/dashboard/planning/calendar",
                                                          "Календарь"),
    ("POST", f"{P}/planning/plans/import"): Action("Плановые платежи загружены списком", "/dashboard/planning/calendar",
                                                   "Календарь"),
    ("POST", f"{P}/planning/calendar/override"): Action("Изменён платёж в календаре", "/dashboard/planning/calendar",
                                                        "Календарь"),
    ("POST", f"{P}/planning/calendar/paid"): Action("Платёж отмечен оплаченным", "/dashboard/planning/calendar",
                                                    "Календарь"),
    ("POST", f"{P}/planning/calendar/unpaid"): Action("Снята отметка об оплате", "/dashboard/planning/calendar",
                                                      "Календарь"),
    ("POST", f"{P}/planning/rules"): Action("Добавлено правило разнесения", "/dashboard/operations/rules", "Правила"),
    ("PATCH", f"{P}/planning/rules/{{rule_id}}"): Action("Изменено правило разнесения", "/dashboard/operations/rules",
                                                         "Правила"),
    ("DELETE", f"{P}/planning/rules/{{rule_id}}"): Action("Удалено правило разнесения", "/dashboard/operations/rules",
                                                          "Правила"),
    ("POST", f"{P}/planning/rules/reorder"): Action("Изменён порядок правил разнесения", "/dashboard/operations/rules",
                                                    "Правила"),
    ("POST", f"{P}/bank/statements/upload"): Action("Загружена выписка банка", "/dashboard/operations/accounts", "Счета"),
    ("POST", f"{P}/bank/matches/merge"): Action("Объединены дубли операций", "/dashboard/operations", "Операции"),
    ("POST", f"{P}/bank/matches/dismiss"): Action("Операции отмечены разными", "/dashboard/operations", "Операции"),
}
REPORT_UPLOAD = ("POST", f"{P}/reports/upload")  # загрузка — событие отчёта; не загрузился — ошибка загрузки
# не события ленты: вход и токены; «Обновить» (синхронизация — свои события); предпросмотры и расчёты (в журнал не
# пишутся); цены и акции через сервис — свои события с подробностями; «Ок» и «Скрыть»; файлы ошибок; служебное
IGNORED: set[tuple[str, str]] = {
    ("POST", f"{P}/auth/register"), ("POST", f"{P}/auth/login"), ("POST", f"{P}/auth/refresh"),
    ("POST", f"{P}/auth/logout"),
    ("POST", f"{P}/credentials/{{cred_id}}/verify"),
    ("POST", f"{P}/sync/products"), ("POST", f"{P}/sync/stocks"), ("POST", f"{P}/sync/balance"),
    ("POST", f"{P}/sync/promotions"), ("POST", f"{P}/sync/postings"), ("POST", f"{P}/sync/all"),
    ("POST", f"{P}/finance/sync"), ("POST", f"{P}/sales/rebuild"),
    ("POST", f"{P}/bank/connections/sync"), ("POST", f"{P}/bank/connections/{{conn_id}}/sync"),
    ("POST", f"{P}/bank/connections/{{conn_id}}/verify"),
    ("POST", f"{P}/price/calculate"), ("POST", f"{P}/prices/preview"), ("POST", f"{P}/prices/apply"),
    ("POST", f"{P}/prices/rollback/{{change_id}}"), ("POST", f"{P}/prices/bulk/preview"),
    ("POST", f"{P}/prices/bulk/upload"), ("POST", f"{P}/prices/bulk/upload/errors"), ("POST", f"{P}/prices/bulk/apply"),
    ("POST", f"{P}/promotions/{{promotion_id}}/products/{{product_id}}/join"),
    ("POST", f"{P}/promotions/{{promotion_id}}/products/{{product_id}}/leave"),
    ("POST", f"{P}/anomalies/acknowledge-all"), ("POST", f"{P}/anomalies/{{anomaly_id}}/acknowledge"),
    ("POST", f"{P}/recommendations/hide"), ("POST", f"{P}/recommendations/unhide"),
    ("POST", f"{P}/notifications/link-code"), ("POST", f"{P}/notifications/test"),
    ("POST", f"{P}/reports/parse"),
    ("POST", f"{P}/costs/import/errors"), ("POST", f"{P}/tax-profile/preview"),
    ("POST", f"{P}/planning/operations/import/errors"), ("POST", f"{P}/planning/plans/import/errors"),
    ("POST", f"{P}/planning/rules/preview"),
}
COSTS_TEMPLATE = f"{P}/costs/{{offer_id:path}}"


def _path_id(template: str, path: str) -> str | None:
    """Значение первого {параметра} шаблона в фактическом пути: /api/v1/reports/{report_id} + /api/v1/reports/7 → 7."""
    head = template.split("{", 1)[0]
    if "{" not in template or not path.startswith(head):
        return None
    rest = path[len(head):]
    tail = template.split("}", 1)[1]
    return unquote(rest[: len(rest) - len(tail)] if tail and rest.endswith(tail) else rest) or None


def costs_path(offer_id: str) -> str:
    return f"{P}/costs/{offer_id}"


def action(key: str, at: datetime, method: str, template: str, path: str | None) -> Event | None:
    """Запись журнала запросов → событие «действие»; не событие ленты — None."""
    a = ACTIONS.get((method, template))
    if a is None:
        return None
    title, link = a.title, a.link
    pid = _path_id(template, path or "")
    if template == COSTS_TEMPLATE and pid:
        title, link = f"Изменена себестоимость {pid}", f"/dashboard/products/costs?search={quote(pid)}"
    elif a.with_id and pid:
        link = f"{a.link}/{pid}"
    return Event(key, at, ACTION, title, "через сервис", None, None, link, a.label)


def upload_failed(key: str, at: datetime) -> Event:
    return Event(key, at, ERROR, "Отчёт не загрузился", "файл не похож на отчёт Ozon или повреждён — загрузите его снова",
                 BAD, None, "/dashboard/analytics/reports/upload", "Загрузка отчётов")
