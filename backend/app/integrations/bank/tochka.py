"""Точка — Tochka.API v1 (спецификация OpenAPI v1.102.0 — `docs/banks/tochka/`, выжимка — `docs/banks/TOCHKA.md`).

Методы (сверены со спецификацией):
- `GET /open-banking/v1.0/accounts` — счета: `accountId` («номер/БИК»), `currency`, `status`, `accountDetails`;
- `GET /open-banking/v1.0/balances` — остатки всех счетов; берём `ClosingAvailable` (доступный остаток);
- `POST /open-banking/v1.0/statements` → `statementId`; `GET /open-banking/v1.0/accounts/{accountId}/statements/{id}` —
  выписка асинхронно: опрашиваем до `Ready` (`Error` — ошибка); в ответе `startDateBalance`, `endDateBalance`,
  `Transaction[]`. Берём только `Booked` — окончательные операции.

Авторизация — JWT-ключ, который клиент выпускает в интернет-банке («Сервисы» → «Интеграции и API» → «Создать JWT-ключ»,
разрешения задаются там же — оставляем только чтение: ReadAccountsBasic, ReadAccountsDetail, ReadBalances,
ReadStatements); `Authorization: Bearer <jwt>`, consent не нужен (docs/banks/tochka/jwt.txt, сверено 15.10.2026).
Песочница — `https://enter.tochka.com/sandbox/v2`, токен `sandbox.jwt.token`: реализация на ней проверена.
Лимиты запросов в документации не нашли — держимся 10 запросов/с.
"""
from datetime import date
from decimal import Decimal

from app.core.config import settings
from app.integrations.bank.base import (
    AccountStatement,
    BankAccountInfo,
    BankAPIError,
    BankOperation,
    BankProvider,
    digits,
    money,
)

PREFIX = "/open-banking/v1.0"
POLL_DELAYS = (1, 1, 2, 2, 3, 3, 5, 5, 5, 5, 10, 10, 10, 10, 10)  # до ~1,5 минуты на выписку
NEEDED = "ReadAccountsBasic, ReadAccountsDetail, ReadBalances, ReadStatements"
AUTH = {
    401: "Точка не приняла токен: он неверный, отозван или его срок истёк — выпустите новый в интернет-банке Точки",
    403: f"У токена Точки нет нужных разрешений — нужны только чтение: {NEEDED}",
}


def _split_account(account_id: str) -> tuple[str | None, str | None]:
    number, _, bik = (account_id or "").partition("/")
    return digits(number, 20), digits(bik, 9)


class TochkaProvider(BankProvider):
    code = "tochka"
    title = "Точка"
    legal_name = "ООО «Банк Точка»"
    history_days = 365  # глубина истории в документации не указана — год при подключении
    min_interval = 0.1

    @classmethod
    def default_url(cls) -> str:
        return settings.TOCHKA_API_URL

    async def _get(self, path: str, **kw):
        return await self.http.request("GET", f"{PREFIX}{path}", auth_messages=AUTH, **kw)

    async def list_accounts(self) -> list[BankAccountInfo]:
        body = await self._get("/accounts")
        out = []
        for a in ((body or {}).get("Data") or {}).get("Account") or []:
            if a.get("status") not in (None, "Enabled"):
                continue
            number, bik = _split_account(a.get("accountId", ""))
            for d in a.get("accountDetails") or []:
                number = number or digits(d.get("identification"), 20)
            if number is None:
                continue
            name = next((d.get("name") for d in a.get("accountDetails") or [] if d.get("name")), None)
            out.append(BankAccountInfo(bank_id=a["accountId"], number=number, currency=a.get("currency") or "RUB",
                                       bik=bik, bank_name=self.legal_name, name=name))
        return out

    async def balances(self, accounts: list[BankAccountInfo]) -> dict[str, Decimal]:
        body = await self._get("/balances")
        out: dict[str, Decimal] = {}
        for b in ((body or {}).get("Data") or {}).get("Balance") or []:
            if b.get("type") != "ClosingAvailable":
                continue
            amount = money((b.get("Amount") or {}).get("amount"))
            if amount is None:
                continue
            out[b.get("accountId")] = -amount if b.get("creditDebitIndicator") == "Debit" else amount
        return out

    async def statement(self, account: BankAccountInfo, date_from: date, date_to: date) -> AccountStatement:
        init = await self.http.request("POST", f"{PREFIX}/statements", auth_messages=AUTH, json={"Data": {"Statement": {
            "accountId": account.bank_id, "startDateTime": date_from.isoformat(), "endDateTime": date_to.isoformat()}}})
        statement_id = (((init or {}).get("Data") or {}).get("Statement") or {}).get("statementId")
        if not statement_id:
            raise BankAPIError("Точка не вернула номер выписки — попробуйте обновить позже")
        for delay in (0, *POLL_DELAYS):
            if delay:
                await self._sleep(delay)
            body = await self._get(f"/accounts/{account.bank_id}/statements/{statement_id}")
            items = ((body or {}).get("Data") or {}).get("Statement") or []
            st = next((s for s in items if s.get("statementId") in (None, statement_id)), None)
            status = (st or {}).get("status")
            if status == "Ready":
                return AccountStatement(
                    account, date_from, date_to, money(st.get("startDateBalance")), money(st.get("endDateBalance")),
                    [op for t in st.get("Transaction") or [] if (op := _operation(t)) is not None],
                )
            if status == "Error":
                raise BankAPIError("Точка не смогла сформировать выписку — попробуйте обновить позже")
        raise BankAPIError("Точка долго формирует выписку — обновим при следующей синхронизации")


def _operation(t: dict) -> BankOperation | None:
    if t.get("status") != "Booked":
        return None  # Pending — ещё не проведена: придёт следующей синхронизацией
    amount = money((t.get("Amount") or {}).get("amount"))
    day = t.get("documentProcessDate")
    if amount is None or not day:
        return None
    incoming = t.get("creditDebitIndicator") == "Credit"
    side = "Debtor" if incoming else "Creditor"  # контрагент: при приходе — плательщик, при расходе — получатель
    party = t.get(f"{side}Party") or {}
    account = t.get(f"{side}Account") or {}
    agent = t.get(f"{side}Agent") or {}
    bank_id = t.get("transactionId") or t.get("paymentId")
    if not bank_id:
        bank_id = f"{day}:{t.get('documentNumber') or '-'}:{amount}:{account.get('identification') or '-'}"
    return BankOperation(
        bank_id=str(bank_id), day=date.fromisoformat(str(day)[:10]), amount=amount if incoming else -amount,
        counterparty=(party.get("name") or "").strip() or None, counterparty_inn=digits(party.get("inn")),
        counterparty_account=digits(account.get("identification"), 20), counterparty_bik=digits(agent.get("identification"), 9),
        purpose=(t.get("description") or "").strip() or None, doc_number=(t.get("documentNumber") or "").strip() or None,
        doc_kind=t.get("transactionTypeCode"), raw=t,
    )
