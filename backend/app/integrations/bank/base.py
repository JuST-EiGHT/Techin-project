"""Банк по API (шаг 3.12) — общий интерфейс провайдеров и HTTP-клиент с повторами.

`BankProvider` — как `CostProvider` у парсеров: проверить доступ, список счетов, выписка за период (внутри — страницы
или асинхронная выписка банка), остатки. Новый банк — новая реализация в `PROVIDERS` (`integrations/bank/__init__.py`).

Права — только чтение (счета, остатки, выписки): платежи из сервиса не отправляем (docs/MONEY.md §6).
Токен живёт только в заголовке запроса: в логи, тексты ошибок и repr не попадает (правило 7).

Повторы, как у клиента Ozon: сеть и таймауты — 3 повтора (1, 2, 4 с); 429 — до 5 повторов (Retry-After, не больше 60 с,
или 1, 2, 4, 8, 16 с); 5xx — 2 повтора через 3 с; 401 / 403 и прочие 4xx — сразу ошибка с понятным текстом.
"""
import asyncio
import base64
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

log = logging.getLogger(__name__)

NETWORK_BACKOFF = (1, 2, 4)
RATE_LIMIT_BACKOFF = (1, 2, 4, 8, 16)
RETRY_AFTER_MAX = 60
SERVER_RETRIES = 2
SERVER_RETRY_DELAY = 3
TIMEOUT = 60.0
RUB = ("RUB", "RUR", "643")

Sleep = Callable[[float], Awaitable[Any]]


# ---------- ошибки: тексты — для пользователя, на русском, без токена ----------


class BankAPIError(Exception):
    """Ошибка банка. `message` — для пользователя; `bank_message` — текст банка как есть (для логов, токен вырезан)."""

    default_message = "Банк отклонил запрос"

    def __init__(self, message: str | None = None, *, status_code: int | None = None, bank_message: str = "",
                 path: str = "") -> None:
        self.message = message or self.default_message
        self.status_code = status_code
        self.bank_message = bank_message
        self.path = path
        super().__init__(self.message)


class BankAuthError(BankAPIError):
    """Токен не принят (401) или у него нет нужных прав (403). Подключение — «нужен новый токен»."""

    default_message = "Банк не принял токен"


class BankRateLimitError(BankAPIError):
    default_message = "Банк ограничил частоту запросов, попробуйте через минуту"


class BankServerError(BankAPIError):
    default_message = "Банк временно не отвечает (ошибка на стороне банка), попробуйте позже"


class BankTimeoutError(BankAPIError):
    default_message = "Банк не ответил вовремя, попробуйте позже"


class BankNetworkError(BankAPIError):
    default_message = "Нет связи с банком, попробуйте позже"


# ---------- данные ----------


@dataclass
class BankAccountInfo:
    bank_id: str  # id счёта в банке (у Точки — «номер/БИК», у Т-Банка — номер)
    number: str  # номер счёта, 20 цифр
    currency: str = "RUB"
    bik: str | None = None
    bank_name: str | None = None
    name: str | None = None  # название счёта в банке, если есть

    @property
    def rub(self) -> bool:
        return (self.currency or "RUB").upper() in RUB


@dataclass
class BankOperation:
    """Операция в окончательном статусе (проведена банком): + приход, − расход."""

    bank_id: str  # id операции в банке — ключ против дублей
    day: date
    amount: Decimal
    counterparty: str | None = None
    counterparty_inn: str | None = None
    counterparty_account: str | None = None
    counterparty_bik: str | None = None
    purpose: str | None = None
    doc_number: str | None = None
    doc_kind: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AccountStatement:
    account: BankAccountInfo
    date_from: date
    date_to: date
    opening: Decimal | None  # на начало date_from; None — банк не сообщил
    closing: Decimal | None  # на конец date_to
    operations: list[BankOperation]


@dataclass
class Access:
    """Результат проверки токена: счета, которые видит токен, и срок токена (если банк или сам токен его сообщает)."""

    accounts: list[BankAccountInfo]
    expires_at: datetime | None = None


def jwt_expiry(token: str) -> datetime | None:
    """Срок из поля `exp` JWT-токена (без проверки подписи — только чтобы показать «действует до»)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        exp = payload.get("exp") if isinstance(payload, dict) else None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    except (ValueError, TypeError, OverflowError):
        return None


def money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except ArithmeticError:
        return None


def digits(value: Any, size: int | None = None) -> str | None:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not text or (size is not None and len(text) != size):
        return None
    return text


# ---------- HTTP ----------


class BankHttp:
    """HTTP-клиент банка: Bearer-токен, повторы, понятные ошибки. `title` — название банка в текстах ошибок."""

    def __init__(self, title: str, base_url: str, token: str, *, min_interval: float = 0.0, retry: bool = True,
                 sleep: Sleep | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.title = title
        self._token = token
        self._retry = retry
        self._sleep: Sleep = sleep if sleep is not None else asyncio.sleep
        self._min_interval = min_interval
        self._last = 0.0
        self._http = httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT, transport=transport,
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def __repr__(self) -> str:
        return f"<BankHttp {self.title}>"

    async def aclose(self) -> None:
        await self._http.aclose()

    def redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _bank_message(self, r: httpx.Response) -> str:
        try:
            body = r.json()
        except ValueError:
            return self.redact(r.text[:500])
        parts: list[str] = []
        if isinstance(body, dict):
            for key in ("message", "errorMessage", "error_description", "error", "detail"):
                if isinstance(body.get(key), str):
                    parts.append(body[key])
            for err in body.get("Errors") or body.get("errors") or []:
                if isinstance(err, dict) and isinstance(err.get("message"), str):
                    parts.append(err["message"])
        return self.redact("; ".join(parts)[:500])

    async def _throttle(self) -> None:
        if self._min_interval:
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last = time.monotonic()

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                      auth_messages: dict[int, str] | None = None) -> Any:
        """Запрос к банку. Возвращает разобранный JSON или бросает BankAPIError и наследников.
        auth_messages — тексты для 401 / 403 своего банка (где выпустить токен, какие нужны права)."""
        net = server = rate = 0
        while True:
            await self._throttle()
            started = time.monotonic()
            try:
                r = await self._http.request(method, path, params=params, json=json)
            except httpx.TransportError as e:
                ms = (time.monotonic() - started) * 1000
                if self._retry and net < len(NETWORK_BACKOFF):
                    delay = NETWORK_BACKOFF[net]
                    net += 1
                    log.warning("%s %s %s: %s за %.0f мс, повтор %d через %s с", self.title, method, path,
                                type(e).__name__, ms, net, delay)
                    await self._sleep(delay)
                    continue
                log.error("%s %s %s: %s за %.0f мс, повторы исчерпаны", self.title, method, path, type(e).__name__, ms)
                error = BankTimeoutError if isinstance(e, httpx.TimeoutException) else BankNetworkError
                raise error(f"{error.default_message} ({self.title})", path=path) from None
            ms = (time.monotonic() - started) * 1000
            status = r.status_code
            if status < 300:
                log.info("%s %s %s → %s за %.0f мс", self.title, method, path, status, ms)
                try:
                    return r.json()
                except ValueError:
                    raise BankAPIError(f"{self.title} вернул ответ в неожиданном формате", status_code=status,
                                       path=path) from None
            bank_message = self._bank_message(r)
            log.warning("%s %s %s → %s за %.0f мс: %s", self.title, method, path, status, ms, bank_message)
            if status in (401, 403):
                default = (f"{self.title} не принял токен: он неверный, отозван или его срок истёк — выпустите новый"
                           if status == 401 else f"{self.title} отказал в доступе: у токена нет прав на чтение выписок")
                message = (auth_messages or {}).get(status, default)
                raise BankAuthError(message, status_code=status, bank_message=bank_message, path=path)
            if status == 429:
                if self._retry and rate < len(RATE_LIMIT_BACKOFF):
                    delay = _retry_after(r)
                    delay = RATE_LIMIT_BACKOFF[rate] if delay is None else delay
                    rate += 1
                    await self._sleep(delay)
                    continue
                raise BankRateLimitError(f"{self.title} ограничил частоту запросов, попробуйте через минуту",
                                         status_code=status, bank_message=bank_message, path=path)
            if status >= 500:
                if self._retry and server < SERVER_RETRIES:
                    server += 1
                    await self._sleep(SERVER_RETRY_DELAY)
                    continue
                raise BankServerError(f"{self.title} временно не отвечает (ошибка на стороне банка), попробуйте позже",
                                      status_code=status, bank_message=bank_message, path=path)
            raise BankAPIError(f"{self.title} отклонил запрос (HTTP {status})" + (f": {bank_message}" if bank_message else ""),
                               status_code=status, bank_message=bank_message, path=path)


def _retry_after(r: httpx.Response) -> float | None:
    try:
        value = float(r.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return min(value, RETRY_AFTER_MAX) if value >= 0 else None


# ---------- провайдер ----------


class BankProvider(ABC):
    """Банк по API. Реализации — `tbank.py`, `tochka.py`."""

    code: str = ""
    title: str = ""
    legal_name: str = ""  # название банка для новых счетов «из банка»
    history_days: int = 365  # сколько истории запрашивать при подключении
    history_from: date | None = None  # банк отдаёт данные не раньше этой даты

    def __init__(self, token: str, *, base_url: str | None = None, sleep: Sleep | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, retry: bool = True) -> None:
        self._sleep: Sleep = sleep if sleep is not None else asyncio.sleep
        self.http = BankHttp(self.title, base_url or self.default_url(), token, min_interval=self.min_interval,
                             retry=retry, sleep=self._sleep, transport=transport)
        self._token_expiry = jwt_expiry(token)

    min_interval: float = 0.0

    @classmethod
    @abstractmethod
    def default_url(cls) -> str: ...

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"

    async def __aenter__(self) -> "BankProvider":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.http.aclose()

    def history_start(self, today: date) -> date:
        start = date.fromordinal(today.toordinal() - self.history_days)
        return max(start, self.history_from) if self.history_from else start

    async def check_access(self) -> Access:
        """Токен работает и видит счета. Нет рублёвых счетов — ошибка с понятным текстом."""
        accounts = [a for a in await self.list_accounts() if a.rub]
        if not accounts:
            raise BankAPIError(f"{self.title}: токен работает, но рублёвых счетов не видно — проверьте, к какой компании "
                               "выпущен токен")
        return Access(accounts, self._token_expiry)

    @abstractmethod
    async def list_accounts(self) -> list[BankAccountInfo]: ...

    @abstractmethod
    async def statement(self, account: BankAccountInfo, date_from: date, date_to: date) -> AccountStatement: ...

    @abstractmethod
    async def balances(self, accounts: list[BankAccountInfo]) -> dict[str, Decimal]:
        """Доступный остаток сейчас: bank_id счёта → сумма."""
