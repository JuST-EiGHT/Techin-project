"""Синхронизация магазина одним взглядом (шаг 10.2): каждый вид данных — когда обновлён, идёт ли загрузка, что не так
простыми словами и что сделать; общее состояние и самые старые данные — для кнопки «Синхронизация» в шапке, «Обновлено …»
в «Аналитике» (10.7) и ошибок синхронизации в «Требует внимания» (10.5).

Вид не обновляется вручную чаще, чем раз в RECENT: кнопка честно говорит «обновлено недавно» — бережём лимиты Ozon,
плановые задачи всё равно обновят данные по расписанию (tasks/celery_app.py).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.models.sync_run import ACTIVE_STATUSES, FAILED, SUCCESS, SyncRun
from app.services import bank_connections
from app.services.sync import runner

OZON_KINDS = ("products", "stocks", "promotions", "finance", "balance", "postings")
BANK = "bank"
KINDS = (*OZON_KINDS, BANK)

TITLES = {
    "products": "Товары и цены",
    "stocks": "Остатки",
    "promotions": "Акции",
    "finance": "Начисления",
    "balance": "Баланс и выплаты",
    "postings": "Отправления",
    BANK: "Банк",
}
# как часто обновляется сам (расписание — tasks/celery_app.py); данные старше двух интервалов — «давно не обновлялись»
SCHEDULE = {
    "products": timedelta(hours=1), "stocks": timedelta(minutes=30), "promotions": timedelta(hours=3),
    "finance": timedelta(hours=6), "balance": timedelta(hours=6), "postings": timedelta(hours=6), BANK: timedelta(hours=1),
}
SCHEDULE_TEXT = {
    "products": "сами — раз в час", "stocks": "сами — раз в 30 минут", "promotions": "сами — раз в 3 часа",
    "finance": "сами — раз в 6 часов", "balance": "сами — раз в 6 часов", "postings": "сами — раз в 6 часов",
    BANK: "сами — раз в час",
}
# вручную — не чаще: лимиты Ozon и банков; отправления и начисления — тяжёлые запросы за много дней
RECENT = {
    "products": timedelta(minutes=10), "stocks": timedelta(minutes=10), "promotions": timedelta(minutes=15),
    "finance": timedelta(minutes=30), "balance": timedelta(minutes=30), "postings": timedelta(minutes=60),
    BANK: timedelta(minutes=10),
}

INTEGRATIONS = "/dashboard/settings/integrations"
BANKS = "/dashboard/settings/integrations?tab=banks"

# состояние вида и общее
FRESH, RUNNING, ERROR, STALE, NEVER, NO_SOURCE = "fresh", "running", "error", "stale", "never", "no_source"


@dataclass(frozen=True)
class Problem:
    text: str  # что случилось — простыми словами
    hint: str  # что сделать
    action: tuple[str, str] | None = None  # (подпись, адрес)


PROBLEMS = {
    runner.NO_KEY: Problem("Ключ Ozon не подключён или истёк", "Добавьте действующий ключ — загрузка продолжится сама.",
                           ("Подключить ключ", INTEGRATIONS)),
    runner.KEY: Problem("Ozon не принял ключ: он истёк, отключён или неверный", "Замените ключ в «Интеграциях».",
                        ("Заменить ключ", INTEGRATIONS)),
    runner.KEY_ROLE: Problem("У ключа Ozon нет прав на эти данные",
                             "Выпустите ключ с нужными правами (в кабинете Ozon: Настройки → Seller API) и замените его.",
                             ("Заменить ключ", INTEGRATIONS)),
    runner.RATE_LIMIT: Problem("Ozon ограничил частоту запросов", "Повторите через пару минут — или дождитесь обновления "
                                                                 "по расписанию."),
    runner.OZON_DOWN: Problem("Ozon временно не отвечает", "Ничего делать не нужно: повторим по расписанию. Можно нажать "
                                                         "«Обновить» позже."),
    runner.OZON_REJECTED: Problem("Ozon отклонил запрос", "Повторите позже. Если ошибка не уходит — подробности ниже, "
                                                        "покажите их нам."),
    runner.BANK_TOKEN: Problem("Банк не принял токен", "Выпустите новый токен в интернет-банке и замените его.",
                               ("Заменить токен", BANKS)),
    runner.BANK_DOWN: Problem("Банк временно не отвечает", "Ничего делать не нужно: повторим по расписанию."),
    runner.BANK_REJECTED: Problem("Банк отклонил запрос", "Повторите позже или проверьте подключение банка.",
                                  ("Открыть банки", BANKS)),
    runner.INTERNAL: Problem("Внутренняя ошибка сервиса", "Повторите позже — ошибка записана для разработчика."),
    runner.INTERRUPTED: Problem("Загрузка прервалась", "Нажмите «Обновить»."),
    runner.QUEUE: Problem("Фоновые задачи были недоступны", "Нажмите «Обновить»."),
}
STALE_PROBLEM = Problem("Давно не обновлялись", "Нажмите «Обновить». Если не поможет — проверьте ключ в «Интеграциях».")
NEVER_PROBLEM = Problem("Ещё не загружались", "Нажмите «Обновить» — первая загрузка займёт несколько минут.")


def classify(kind: str, run: SyncRun) -> str:
    """Причина провала: записанная при запуске (шаг 10.2) или — у старых запусков — по тексту ошибки."""
    recorded = (run.stats or {}).get("error_kind")
    if recorded in PROBLEMS:
        return recorded
    text = (run.error or "").lower()
    if kind == BANK:
        return runner.BANK_TOKEN if "токен" in text else runner.BANK_DOWN if "не отвечает" in text else runner.BANK_REJECTED
    for needle, reason in (("не подключён действующий", runner.NO_KEY), ("нет прав", runner.KEY_ROLE),
                           ("нет нужной роли", runner.KEY_ROLE), ("ключ", runner.KEY),
                           ("частоту", runner.RATE_LIMIT), ("не отвечает", runner.OZON_DOWN),
                           ("не ответил", runner.OZON_DOWN), ("нет связи", runner.OZON_DOWN),
                           ("прервалась", runner.INTERRUPTED), ("фоновые задачи", runner.QUEUE),
                           ("внутренняя", runner.INTERNAL)):
        if needle in text:
            return reason
    return runner.OZON_REJECTED


def _utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class KindState:
    kind: str
    state: str
    last_success_at: datetime | None
    running: bool
    problem: Problem | None
    error: str | None  # текст последней ошибки — подробности
    recent_until: datetime | None  # до этого времени вручную не обновляем: данные только что загружены
    blocked: bool = False  # не обновить, пока не исправят (нет ключа, банку нужен новый токен)

    @property
    def title(self) -> str:
        return TITLES[self.kind]


def kind_state(kind: str, last: SyncRun | None, success_at: datetime | None, now: datetime,
               blocked: Problem | None = None) -> KindState:
    """Состояние вида: идёт загрузка → последняя попытка с ошибкой → не загружались → давно → свежие.
    blocked — вид сейчас не обновить (нет ключа, банку нужен новый токен): это и есть проблема."""
    success_at = _utc(success_at)
    running = last is not None and last.status in ACTIVE_STATUSES
    recent = success_at + RECENT[kind] if success_at and success_at + RECENT[kind] > now else None
    failed = last is not None and last.status == FAILED
    error = last.error if failed else None
    if running:
        return KindState(kind, RUNNING, success_at, True, None, None, recent)
    if blocked is not None:
        return KindState(kind, ERROR, success_at, False, blocked, error, None, blocked=True)
    if failed:
        return KindState(kind, ERROR, success_at, False, PROBLEMS[classify(kind, last)], error, recent)
    if success_at is None:
        return KindState(kind, NEVER, None, False, NEVER_PROBLEM, None, None)
    if now - success_at > 2 * SCHEDULE[kind]:
        return KindState(kind, STALE, success_at, False, STALE_PROBLEM, None, None)
    return KindState(kind, FRESH, success_at, False, None, None, recent)


def overall(states: list[KindState]) -> str:
    """Общее состояние кнопки: идёт загрузка → есть ошибка → не всё свежее → всё свежее; нечего загружать — no_source."""
    if not states:
        return NO_SOURCE
    found = {s.state for s in states}
    for state in (RUNNING, ERROR):
        if state in found:
            return state
    return STALE if found & {STALE, NEVER} else FRESH


def oldest(states: list[KindState]) -> tuple[str | None, datetime | None]:
    """Самые старые данные: вид, который ещё не загружался, или с самым давним обновлением."""
    # ещё не загружался; идущая сейчас первая загрузка — после остальных
    never = sorted((s for s in states if s.last_success_at is None), key=lambda s: s.running)
    if never:
        return never[0].kind, None
    if not states:
        return None, None
    s = min(states, key=lambda x: x.last_success_at)
    return s.kind, s.last_success_at


@dataclass
class Overview:
    has_active_key: bool
    has_bank: bool
    states: list[KindState]  # только виды, которые у магазина есть: Ozon — если был ключ, банк — если подключён
    state: str
    oldest_kind: str | None
    oldest_at: datetime | None


async def _last(db: AsyncSession, user_id: uuid.UUID, kind: str) -> tuple[SyncRun | None, datetime | None]:
    await runner.active_run(db, user_id, kind)  # зависший запуск — проваленный («прервалась»)
    last = await runner.latest_run(db, user_id, kind)
    success = await runner.latest_run(db, user_id, kind, status=SUCCESS)
    return last, success.finished_at if success else None


async def build(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> Overview:
    """Обзор магазина. Ключа Ozon никогда не было (только отчёты XLSX) — виды Ozon не показываем и ошибкой не считаем;
    был, но истёк или отключён — это проблема каждого вида Ozon («Заменить ключ»)."""
    now = now or datetime.now(timezone.utc)
    has_key = await crud_credential.has_active(db, user_id)
    had_key = has_key or bool(await crud_credential.list_for_user(db, user_id))
    connections = await bank_connections.list_connections(db, user_id)
    states: list[KindState] = []
    if had_key:
        blocked = None if has_key else PROBLEMS[runner.NO_KEY]
        for kind in OZON_KINDS:
            states.append(kind_state(kind, *await _last(db, user_id, kind), now, blocked))
    if connections:
        # все подключения ждут новый токен — банк не обновить, пока его не заменят
        blocked = PROBLEMS[runner.BANK_TOKEN] if all(c.status == "auth" for c in connections) else None
        state = kind_state(BANK, *await _last(db, user_id, BANK), now, blocked)
        if state.problem is None and any(c.status == "auth" for c in connections):
            state.state, state.problem = ERROR, PROBLEMS[runner.BANK_TOKEN]  # один из банков — без токена
        states.append(state)
    kind, at = oldest(states)
    return Overview(has_key, bool(connections), states, overall(states), kind, at)
