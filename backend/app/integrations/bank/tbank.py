"""Т-Банк — T-API для бизнеса (`https://business.tbank.ru/openapi`).

Сверено с официальной документацией (developer.tbank.ru, через поиск — сама страница из облачной среды не открывается):
- выписка — `GET /api/v1/statement` (прежний `/api/v1/bank-statement` помечен устаревшим): операции за период
  порциями до `limit` (до 5000); не поместились — в ответе `nextCursor`, его передаём в `cursor`; `withBalances=true` —
  остатки в ответе (только в первой порции); данные — с июня 2023; лимит — 20 запросов в секунду;
- токен выпускает сам клиент в Т-Бизнесе («Интеграции → T-API»), доступ «Счета и выписки → Информация об операциях
  компании»; токен действует 90 дней с последнего использования.

НЕ СВЕРЕНО (страница метода не открылась): имена полей операции и остатков, формат дат в `from` / `to`, метод списка
счетов `GET /api/v4/bank-accounts`. Поля ниже — по памяти о T-API; сверить по выгрузке документации в
`docs/banks/tbank/` и поправить только этот модуль (docs/banks/TBANK.md, «Открытые вопросы»).
"""
from datetime import date
from decimal import Decimal

from app.core.config import settings
from app.integrations.bank.base import AccountStatement, BankAccountInfo, BankOperation, BankProvider, digits, money

PAGE = 5000
MAX_PAGES = 200  # защита от бесконечного курсора
AUTH = {
    401: "Т-Банк не принял токен: он неверный, отозван или не использовался больше 90 дней — выпустите новый в Т-Бизнесе "
         "(«Интеграции → T-API»)",
    403: "У токена Т-Банка нет нужного доступа — при выпуске отметьте «Счета и выписки»: информация о счетах и об операциях "
         "компании; если в токене указаны IP-адреса — уберите ограничение",
}


class TBankProvider(BankProvider):
    code = "tbank"
    title = "Т-Банк"
    legal_name = "АО «ТБанк»"
    history_days = 3 * 365
    history_from = date(2023, 6, 1)  # T-API отдаёт операции с июня 2023
    min_interval = 0.05  # 20 запросов в секунду

    @classmethod
    def default_url(cls) -> str:
        return settings.TBANK_API_URL

    async def _get(self, path: str, params: dict | None = None):
        return await self.http.request("GET", path, params=params, auth_messages=AUTH)

    async def _accounts_raw(self) -> list[dict]:
        body = await self._get("/api/v4/bank-accounts")
        return body if isinstance(body, list) else (body or {}).get("accounts") or []

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
            amount = money((a.get("balance") or {}).get("otb"))  # otb — доступный остаток
            if number and amount is not None:
                out[number] = amount
        return out

    async def statement(self, account: BankAccountInfo, date_from: date, date_to: date) -> AccountStatement:
        params = {"accountNumber": account.number, "from": f"{date_from.isoformat()}T00:00:00+03:00",
                  "to": f"{date_to.isoformat()}T23:59:59+03:00", "limit": PAGE, "withBalances": "true"}
        operations: list[BankOperation] = []
        opening = closing = None
        for page in range(MAX_PAGES):
            body = await self._get("/api/v1/statement", params) or {}
            if page == 0:
                bal = body.get("balances") or {}
                opening, closing = money(bal.get("incoming")), money(bal.get("outgoing"))
            operations += [op for o in body.get("operations") or [] if (op := _operation(o)) is not None]
            cursor = body.get("nextCursor")
            if not cursor:
                break
            params = {**{k: v for k, v in params.items() if k != "withBalances"}, "cursor": cursor}
        return AccountStatement(account, date_from, date_to, opening, closing, operations)


def _operation(o: dict) -> BankOperation | None:
    amount = money(o.get("accountAmount") if o.get("accountAmount") is not None else o.get("operationAmount"))
    day = o.get("operationDate") or o.get("chargeDate") or o.get("drawDate")
    if amount is None or not day or not o.get("operationId"):
        return None
    incoming = (o.get("typeOfOperation") or "").lower() == "credit"
    party = (o.get("payer") if incoming else o.get("receiver")) or {}
    return BankOperation(
        bank_id=str(o["operationId"]), day=date.fromisoformat(str(day)[:10]), amount=abs(amount) if incoming else -abs(amount),
        counterparty=(party.get("name") or "").strip() or None, counterparty_inn=digits(party.get("inn")),
        counterparty_account=digits(party.get("acct"), 20), counterparty_bik=digits(party.get("bicRu"), 9),
        purpose=(o.get("payPurpose") or o.get("description") or "").strip() or None,
        doc_number=(str(o.get("documentNumber") or "")).strip() or None, doc_kind=o.get("category"), raw=o,
    )
