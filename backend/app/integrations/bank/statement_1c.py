"""Выписка банка в формате обмена с 1С (`1CClientBankExchange`, версии 1.02 / 1.03) — шаг 3.11.

Файл — строки «Ключ=Значение»: заголовок, секции счетов («СекцияРасчСчет» … «КонецРасчСчет») с остатками и документы
(«СекцияДокумент=…» … «КонецДокумента»). Кодировка — windows-1251 (обычно, «Кодировка=Windows»), utf-8 или DOS (cp866).
Приход или расход — по тому, наш ли счёт у плательщика: наш счёт плательщика — расход, наш счёт получателя — приход;
ни тот ни другой — документ не по счетам выписки, пропускаем с предупреждением.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

HEADER = "1CClientBankExchange"
NOT_1C = (
    "Это не выписка в формате 1С: файл должен начинаться со строки «1CClientBankExchange». В интернет-банке выберите "
    "выгрузку выписки «в 1С» (обычно файл .txt)."
)


class StatementError(Exception):
    """Файл целиком не подходит. Текст — для пользователя."""


@dataclass
class AccountSection:
    number: str
    date_from: date | None = None
    date_to: date | None = None
    opening: Decimal | None = None  # остаток на начало дня date_from
    closing: Decimal | None = None  # остаток на конец дня date_to
    income: Decimal | None = None  # ВсегоПоступило
    expense: Decimal | None = None  # ВсегоСписано


@dataclass
class Document:
    line: int  # строка начала документа в файле — для понятных ошибок
    kind: str  # вид документа: «Платежное поручение» …
    number: str | None
    day: date
    amount: Decimal  # + приход, − расход
    account: str  # наш счёт
    counterparty: str | None
    counterparty_inn: str | None
    counterparty_account: str | None
    counterparty_bik: str | None
    purpose: str | None
    own_bik: str | None = None
    own_bank: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Statement:
    version: str | None
    encoding: str
    date_from: date | None
    date_to: date | None
    accounts: list[AccountSection]
    documents: list[Document]
    skipped: list[str] = field(default_factory=list)  # документы не по счетам выписки и с ошибками — строкой
    warnings: list[str] = field(default_factory=list)

    @property
    def numbers(self) -> list[str]:
        return [a.number for a in self.accounts]


def decode(content: bytes) -> tuple[str, str]:
    """Текст и кодировка: utf-8 (с BOM или без), windows-1251 или DOS (cp866) — по строке «Кодировка»."""
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8"), "utf-8"
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    text = content.decode("cp1251", errors="replace")
    for line in text.splitlines()[:30]:
        if line.startswith("Кодировка=") and line.split("=", 1)[1].strip().upper() == "DOS":
            return content.decode("cp866", errors="replace"), "cp866"
    if "Кодировка=DOS".encode("cp866") in content[:3000]:
        return content.decode("cp866", errors="replace"), "cp866"  # «Кодировка=DOS» в самой DOS-кодировке
    return text, "cp1251"


def _day(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(value)


def _money(value: str | None) -> Decimal | None:
    value = (value or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not value:
        return None
    try:
        return Decimal(value).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(value) from exc


def _account(value: str | None) -> str | None:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits or None


def _name(raw: dict, side: str) -> str | None:
    """Наименование плательщика или получателя: «Плательщик1» или «Плательщик» без «ИНН … » в начале."""
    name = (raw.get(f"{side}1") or "").strip()
    if not name:
        name = (raw.get(side) or "").strip()
        if name.upper().startswith("ИНН"):
            parts = name.split(maxsplit=2)
            name = parts[2] if len(parts) > 2 else ""
    return name or None


def _purpose(raw: dict) -> str | None:
    parts = [raw.get("НазначениеПлатежа", "")] + [raw.get(f"НазначениеПлатежа{i}", "") for i in range(1, 7)]
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return text[:2000] or None


def parse(content: bytes) -> Statement:
    if not content:
        raise StatementError("Файл пустой.")
    text, encoding = decode(content)
    lines = text.splitlines()
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if first.lstrip("﻿") != HEADER:
        raise StatementError(NOT_1C)

    header: dict[str, str] = {}
    header_accounts: list[str] = []
    sections: list[dict] = []
    documents: list[tuple[int, str, dict]] = []
    current: dict | None = None
    current_kind: str | None = None
    start = 0
    for i, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line == HEADER:
            continue
        if line == "СекцияРасчСчет":
            current, current_kind = {}, "account"
            continue
        if line == "КонецРасчСчет":
            if current is not None and current_kind == "account":
                sections.append(current)
            current = current_kind = None
            continue
        if line.startswith("СекцияДокумент"):
            current, current_kind, start = {"_вид": line.split("=", 1)[1].strip() if "=" in line else ""}, "doc", i
            continue
        if line == "КонецДокумента":
            if current is not None and current_kind == "doc":
                documents.append((start, current.pop("_вид", ""), current))
            current = current_kind = None
            continue
        if line == "КонецФайла":
            break
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if current is not None:
            current[key] = value
        elif key == "РасчСчет":
            header_accounts.append(value)
        else:
            header[key] = value

    try:
        date_from, date_to = _day(header.get("ДатаНачала")), _day(header.get("ДатаКонца"))
    except ValueError as exc:
        raise StatementError(f"Период выписки: не похоже на дату «{exc}» — нужна ДД.ММ.ГГГГ") from exc

    accounts: dict[str, AccountSection] = {}
    for number in header_accounts:
        if (n := _account(number)) and n not in accounts:
            accounts[n] = AccountSection(n, date_from, date_to)
    for sec in sections:
        n = _account(sec.get("РасчСчет"))
        if not n:
            continue
        a = accounts.setdefault(n, AccountSection(n, date_from, date_to))
        try:
            a.date_from = _day(sec.get("ДатаНачала")) or a.date_from
            a.date_to = _day(sec.get("ДатаКонца")) or a.date_to
            a.opening = _money(sec.get("НачальныйОстаток"))
            a.closing = _money(sec.get("КонечныйОстаток"))
            a.income = _money(sec.get("ВсегоПоступило"))
            a.expense = _money(sec.get("ВсегоСписано"))
        except ValueError as exc:
            raise StatementError(f"Остатки по счёту {n}: не похоже на число или дату «{exc}»") from exc
    if not accounts:
        raise StatementError("В выписке не указан расчётный счёт (строка «РасчСчет=»). Выгрузите выписку по счёту заново.")

    ours = set(accounts)
    statement = Statement(header.get("ВерсияФормата"), encoding, date_from, date_to, list(accounts.values()), [])
    for line_no, kind, raw in documents:
        number = (raw.get("Номер") or "").strip() or None
        label = f"Документ № {number}" if number else f"Документ в строке {line_no}"
        payer = _account(raw.get("ПлательщикСчет") or raw.get("ПлательщикРасчСчет"))
        receiver = _account(raw.get("ПолучательСчет") or raw.get("ПолучательРасчСчет"))
        try:
            amount = _money(raw.get("Сумма"))
            if amount is None or amount <= 0:
                raise ValueError(raw.get("Сумма") or "пусто")
        except ValueError as exc:
            statement.skipped.append(f"{label}: сумма «{exc}» — не похоже на число больше нуля")
            continue
        sides = []
        if payer in ours:
            sides.append(("expense", payer, "Получатель", receiver))
        if receiver in ours:
            sides.append(("income", receiver, "Плательщик", payer))
        if not sides:
            statement.skipped.append(f"{label}: ни плательщик, ни получатель — не счета этой выписки")
            continue
        for direction, own, other, other_account in sides:
            try:
                day = (_day(raw.get("ДатаСписано") if direction == "expense" else raw.get("ДатаПоступило"))
                       or _day(raw.get("Дата")))
            except ValueError as exc:
                statement.skipped.append(f"{label}: дата «{exc}» — нужна ДД.ММ.ГГГГ")
                break
            if day is None:
                statement.skipped.append(f"{label}: нет даты документа")
                break
            own_side = "Плательщик" if direction == "expense" else "Получатель"
            statement.documents.append(Document(
                line=line_no, kind=kind, number=number, day=day,
                amount=amount if direction == "income" else -amount, account=own,
                counterparty=_name(raw, other), counterparty_inn=(raw.get(f"{other}ИНН") or "").strip() or None,
                counterparty_account=other_account,
                counterparty_bik=(raw.get(f"{other}БИК") or "").strip() or None, purpose=_purpose(raw),
                own_bik=(raw.get(f"{own_side}БИК") or "").strip() or None,
                own_bank=(raw.get(f"{own_side}Банк1") or "").strip() or None, raw=raw,
            ))
    if not statement.documents and not statement.skipped:
        statement.warnings.append("В выписке нет операций за период — загрузятся только остатки.")
    return statement
