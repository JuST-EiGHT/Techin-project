"""Т-Банк — T-API для бизнеса (`https://business.tbank.ru/openapi`).

Сверено с официальной спецификацией (`business.tbank.ru/openapi/docs/front/openapi.yaml`, выжимка — docs/banks/TBANK.md,
нужные методы и схемы — `docs/banks/tbank/`):
- счета — `GET /api/v4/bank-accounts` (доступ «Информация о счетах компании», 6 запросов/с): `accountNumber`, `name`,
  `status` (CLSC / CLSB — закрыт), `currency` (код ОКВ, `643`), `bankBik`, `balance.balance` — текущий остаток
  (`otb` — вместе с овердрафтом, его не берём);
- выписка — `GET /api/v1/statement` (доступ «Информация об операциях компании», 20 запросов/с; прежний
  `/api/v1/bank-statement` помечен устаревшим): `from` — включительно, `to` — не включительно, даты и время в ISO 8601
  по UTC; `operationStatus=Transaction` — только проведённые (без авторизаций); порции до `limit` (до 5000), продолжение —
  `nextCursor` → `cursor`; `withBalances=true` — `balances.balanceBegin` / `balanceEnd` (только в первой порции);
  данные — с июня 2023;
- операция: `operationId`, `operationDate` (дата проведения по балансу, UTC), `typeOfOperation` Credit / Debit,
  `accountAmount`, `payPurpose`, `documentNumber`, `docDate`, `counterParty` (`name`, `inn`, `account`, `bankBic`),
  `payer` / `receiver`. `operationId` у операций с 22:00 до 03:00 может смениться — банк советует опознавать проведённую
  операцию по `docDate` + `documentNumber`: из них и сумма — наш ключ против дублей;
- ошибки — `errorCode`, `errorMessage`, `errorId`; токен — Bearer, выпускает сам клиент в Т-Бизнесе
  («Интеграции → T-API»), действует 90 дней с последнего использования; песочница — токен `TBankSandboxToken`.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.core.config import settings
from app.integrations.bank.base import AccountStatement, BankAccountInfo, BankOperation, BankProvider, digits, money

PAGE = 5000
MAX_PAGES = 200  # защита от бесконечного курсора
MSK = timezone(timedelta(hours=3))
CLOSED = ("CLSC", "CLSB")  # закрыт по инициативе клиента / банка
AUTH = {
    401: "Т-Банк не принял токен: он неверный, отозван или не использовался больше 90 дней — выпустите новый в Т-Бизнесе "
         "(«Интеграции → T-API»)",
    403: "У токена Т-Банка нет нужного доступа — при выпуске отметьте «Счета и выписки»: информация о счетах и об операциях "
         "компании; если в токене указаны IP-адреса — уберите ограничение",
}


def _utc(day: date) -> str:
    """Начало дня по Москве — момент в UTC для `from` / `to`."""
    return datetime.combine(day, time(0), MSK).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _msk_day(value: str) -> date:
    """Дата операции по Москве: банк отдаёт время в UTC (проведение по балансу «в полночь» — 21:00 предыдущего дня)."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(MSK).date()


class TBankProvider(BankProvider):
    code = "tbank"
    title = "Т-Банк"
    legal_name = "АО «ТБанк»"
    history_days = 3 * 365
    history_from = date(2023, 6, 1)  # T-API отдаёт операции с июня 2023
    min_interval = 0.2  # счета — 6 запросов/с, выписка — 20: держимся ниже обоих

    def __init__(self, token: str, **kw) -> None:
        super().__init__(token, **kw)
        self._accounts: list[dict] | None = None

    @classmethod
    def default_url(cls) -> str:
        return settings.TBANK_API_URL

    async def _get(self, path: str, params: dict | None = None):
        return await self.http.request("GET", path, params=params, auth_messages=AUTH)

    async def _accounts_raw(self) -> list[dict]:
        if self._accounts is None:  # счета и остатки — одним запросом на обновление
            body = await self._get("/api/v4/bank-accounts")
            self._accounts = [a for a in (body if isinstance(body, list) else []) if a.get("status") not in CLOSED]
        return self._accounts

    async def list_accounts(self) -> list[BankAccountInfo]:
        out = []
        for a in await self._accounts_raw():
            number = digits(a.get("accountNumber"), 20)
            if number is None:
                continue
            currency = str(a.get("currency") or "643")
            out.append(BankAccountInfo(bank_id=number, number=number, currency="RUB" if currency in ("643", "RUB") else currency,
                                       bik=digits(a.get("bankBik"), 9), bank_name=self.legal_name, name=a.get("name")))
        return out

    async def balances(self, accounts: list[BankAccountInfo]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for a in await self._accounts_raw():
            number = digits(a.get("accountNumber"), 20)
            amount = money((a.get("balance") or {}).get("balance"))  # текущий остаток, без овердрафта
            if number and amount is not None:
                out[number] = amount
        return out

    async def statement(self, account: BankAccountInfo, date_from: date, date_to: date) -> AccountStatement:
        params = {"accountNumber": account.number, "from": _utc(date_from), "to": _utc(date_to + timedelta(days=1)),
                  "operationStatus": "Transaction", "limit": PAGE, "withBalances": "true"}
        operations: list[BankOperation] = []
        opening = closing = None
        for page in range(MAX_PAGES):
            body = await self._get("/api/v1/statement", params) or {}
            if page == 0:
                bal = body.get("balances") or {}
                opening, closing = money(bal.get("balanceBegin")), money(bal.get("balanceEnd"))
            operations += [op for o in body.get("operations") or [] if (op := _operation(o)) is not None]
            cursor = body.get("nextCursor")
            if not cursor or cursor == params.get("cursor"):
                break  # всё получено (тот же курсор — защита от зацикливания, так отвечает песочница)
            params = {**{k: v for k, v in params.items() if k != "withBalances"}, "cursor": cursor}
        return AccountStatement(account, date_from, date_to, opening, closing, operations)


def _operation(o: dict) -> BankOperation | None:
    if o.get("operationStatus") not in (None, "Transaction"):
        return None  # авторизация — ещё не проведена
    amount = money(o.get("accountAmount") if o.get("accountAmount") is not None else o.get("operationAmount"))
    when = o.get("operationDate")
    if amount is None or not when or not o.get("operationId"):
        return None
    incoming = (o.get("typeOfOperation") or "").lower() == "credit"
    party = o.get("counterParty") or {}
    side = (o.get("payer") if incoming else o.get("receiver")) or {}
    number = (str(o.get("documentNumber") or "")).strip() or None
    # operationId может смениться (операции с 22:00 до 03:00) — проведённую опознаём по дате документа и номеру
    bank_id = f"doc:{o['docDate']}:{number}:{abs(amount)}:{'c' if incoming else 'd'}" if o.get("docDate") and number \
        else str(o["operationId"])
    return BankOperation(
        bank_id=bank_id, day=_msk_day(when), amount=abs(amount) if incoming else -abs(amount),
        counterparty=(party.get("name") or side.get("name") or "").strip() or None,
        counterparty_inn=digits(party.get("inn") or side.get("inn")),
        counterparty_account=digits(party.get("account") or side.get("acct"), 20),
        counterparty_bik=digits(party.get("bankBic") or side.get("bicRu"), 9),
        purpose=(o.get("payPurpose") or o.get("description") or "").strip() or None,
        doc_number=number, doc_kind=o.get("category"), raw=o,
    )
