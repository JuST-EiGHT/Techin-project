"""Правила разнесения операций по статьям (шаг 3.12) — чистый модуль без БД.

Правило — условия и статья. Все указанные условия должны совпасть:
- направление: поступление (`in`) или платёж (`out`);
- ИНН контрагента — точно;
- название контрагента — все слова правила есть в названии («Петров ИП» = «ИП Петров Петр Петрович»);
- назначение платежа — любая из фраз через запятую встречается в назначении («аренд» найдёт «Аренда склада»);
- сумма от / до — по модулю;
- счёт операции и источник (из банка, из файла операций, вручную) — точно (шаг 10.20).
Правила проверяются по порядку (`priority`, меньше — раньше), срабатывает первое подошедшее. `coverage` — сколько
операций подходит под каждое правило и сколько ему достаётся: правило, все операции которого забирает правило выше,
не срабатывает (предупреждение в журнале применения).

Встроенные правила стоят раньше пользовательских: поступление от маркетплейса по ИНН и перевод между своими счетами —
при загрузке выписки (`services/bank_import`); налоги и взносы по реквизитам казначейства — здесь (`is_tax`).
"""
import json
from dataclasses import dataclass
from decimal import Decimal
from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "bank_rules.json"


@dataclass
class Facts:
    """Что известно об операции: + приход, − расход; ИНН, счёт и назначение — у операций банка."""

    amount: Decimal
    counterparty: str | None = None
    inn: str | None = None
    purpose: str | None = None
    counterparty_account: str | None = None
    account_id: str | None = None  # счёт операции (свой)
    source: str | None = None  # bank | import | manual


@dataclass
class Rule:
    id: str
    direction: str  # in | out
    category_id: str
    priority: int = 0
    counterparty_inn: str | None = None
    counterparty: str | None = None
    purpose: str | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    enabled: bool = True
    account_id: str | None = None
    source: str | None = None


def norm(text: str | None) -> str:
    """Текст для сравнения: без регистра, «ё» = «е», знаки препинания и кавычки — пробелы."""
    clean = "".join(ch if ch.isalnum() else " " for ch in (text or "").lower().replace("ё", "е"))
    return " ".join(clean.split())


def phrases(text: str | None) -> list[str]:
    parts = (text or "").replace(";", ",").replace("\n", ",").split(",")
    return [p for p in (norm(x) for x in parts) if p]


def has_conditions(rule: Rule) -> bool:
    return bool(rule.counterparty_inn or norm(rule.counterparty) or phrases(rule.purpose)
                or rule.amount_min is not None or rule.amount_max is not None or rule.account_id or rule.source)


def fits(rule: Rule, f: Facts) -> bool:
    if not rule.enabled or f.amount == 0 or (f.amount > 0) != (rule.direction == "in") or not has_conditions(rule):
        return False
    if rule.counterparty_inn and rule.counterparty_inn != (f.inn or ""):
        return False
    if rule.account_id and rule.account_id != f.account_id:
        return False
    if rule.source and rule.source != f.source:
        return False
    if norm(rule.counterparty):
        words = set(norm(f.counterparty).split())
        if not words or not set(norm(rule.counterparty).split()) <= words:
            return False
    if (wanted := phrases(rule.purpose)) and not any(p in norm(f.purpose) for p in wanted):
        return False
    value = abs(f.amount)
    if rule.amount_min is not None and value < rule.amount_min:
        return False
    return not (rule.amount_max is not None and value > rule.amount_max)


def pick(rules: list[Rule], f: Facts) -> Rule | None:
    """Первое подошедшее правило по порядку."""
    return next((r for r in sorted(rules, key=lambda r: (r.priority, r.id)) if fits(r, f)), None)


def coverage(rules: list[Rule], facts: list[Facts], winner: Callable[[Facts], str | None]) -> dict[str, dict]:
    """По каждому правилу: `fits` — сколько операций подходит под условия, `wins` — сколько достаётся ему,
    `taken_by` — кто забирает остальные (id правила или «taxes» — встроенное правило налогов). `winner` — что
    выбирает разнесение для операции (с учётом порядка и встроенных правил)."""
    out = {r.id: {"fits": 0, "wins": 0, "taken_by": Counter()} for r in rules}
    for f in facts:
        won = winner(f)
        for r in rules:
            if not fits(r, f):
                continue
            stat = out[r.id]
            stat["fits"] += 1
            if won == r.id:
                stat["wins"] += 1
            elif won is not None:
                stat["taken_by"][won] += 1
    return out


@lru_cache(maxsize=1)
def treasury() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))["treasury"]


def is_tax(f: Facts) -> bool:
    """Платёж в бюджет по реквизитам казначейства: ЕНП и другие налоги и взносы."""
    if f.amount >= 0:
        return False
    t = treasury()
    if f.inn and f.inn in t["inn"]:
        return True
    if f.counterparty_account and any(f.counterparty_account.startswith(p) for p in t["account_prefixes"]):
        return True
    words = norm(f.counterparty).split()
    return any(w.startswith(n) for w in words for n in t["name_words"])
