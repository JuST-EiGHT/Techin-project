"""Банки: выписка файлом в формате 1С (`statement_1c`, шаг 3.11) и банки по API (`base.BankProvider`, шаг 3.12)."""
from app.integrations.bank.base import BankAPIError, BankAuthError, BankProvider
from app.integrations.bank.tbank import TBankProvider
from app.integrations.bank.tochka import TochkaProvider

PROVIDERS: dict[str, type[BankProvider]] = {p.code: p for p in (TBankProvider, TochkaProvider)}


def make_provider(code: str, token: str, **kw) -> BankProvider:
    return PROVIDERS[code](token, **kw)


__all__ = ["PROVIDERS", "BankAPIError", "BankAuthError", "BankProvider", "make_provider"]
