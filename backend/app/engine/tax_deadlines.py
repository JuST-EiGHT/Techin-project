"""Сроки налогов, НДС и взносов (шаг 3.9). Чистый модуль — без БД и HTTP.

Правила — данные с датой действия (`deadlines` в app/data/tax_rules.json, материалы владельца — docs/TAXES.md):
какие платежи у режима, за какой период, до какого числа, срок уведомления или декларации. Срок в выходной или
праздник переносится на следующий рабочий день (НК, ст. 6.1; календарь — app/data/work_calendar.json).

Профиль меняется во времени: каждый отрезок (`Segment`) — свой режим. Платёж за период, который режим действовал не
целиком, считается за его часть (например, УСН с 1 июля по 14 августа и ОСН дальше — два платежа за III квартал).
Суммы считает сервис: здесь только что, за какой период и до какой даты.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.engine.tax import _on, _rules
from app.engine.workdays import next_workday


@dataclass(frozen=True)
class Segment:
    """Отрезок, когда действовал один профиль: [start, end], end=None — до сих пор."""

    start: date
    end: date | None
    regime: str
    legal_form: str | None = None
    vat_mode: str = "none"
    has_employees: bool = False
    contributions: bool = False  # взносы за год указаны в профиле
    ref: Any = None  # запись профиля — сервису для расчёта суммы


@dataclass(frozen=True)
class Obligation:
    key: str  # tax:<график>:<что>:<начало периода>:<срок> — стабилен, к нему привязываются перенос и «оплачено»
    schedule: str  # usn | profit | ndfl_ip | ausn | npd | patent | vat | contributions
    what: str  # tax | vat | patent | contributions | contributions_extra
    title: str
    period_start: date  # за какой период (часть периода, если режим действовал не весь период)
    period_end: date
    full_start: date  # период по правилу — квартал, полугодие, месяц, год
    full_end: date
    due: date  # срок с переносом на рабочий день
    due_rule: date  # срок по правилу — до переноса
    notice: date | None = None  # уведомление или декларация — тоже с переносом
    doc: str | None = None  # «Уведомление», «Декларация», «Декларация по НДС»
    share: Decimal = Decimal(1)  # доля суммы периода: НДС по третям, патент 1/3 и 2/3
    segment: Segment | None = field(default=None, compare=False)
    extra: dict = field(default_factory=dict, compare=False)  # threshold, rate — у взносов 1 %


def rules_on(day: date) -> dict:
    return _on(_rules()["deadlines"], day)


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_month(year: int, month: int, n: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + n
    return index // 12, index % 12 + 1


def _rule_day(end: date, rule: list[int]) -> date:
    """[через сколько месяцев после месяца end, число] → дата; число больше длины месяца — последний день."""
    y, m = _shift_month(end.year, end.month, rule[0])
    return date(y, m, min(rule[1], calendar.monthrange(y, m)[1]))


def _share(text: str | None) -> Decimal:
    if not text:
        return Decimal(1)
    a, b = text.split("/")
    return Decimal(a) / Decimal(b)


def schedules(seg: Segment, day: date) -> list[str]:
    rules = rules_on(day)
    out = list(rules["regimes"].get(seg.regime, []))
    if seg.vat_mode != "none":
        out.append(rules["vat"])
    return out


def _applies(item: dict, seg: Segment) -> bool:
    if "forms" in item and seg.legal_form not in item["forms"]:
        return False
    who = item.get("who")
    ip_alone = seg.legal_form == "ip" and not seg.has_employees
    if who == "employer" and not seg.contributions:
        return False  # взносы за сотрудников — только если указаны; фиксированные ИП — всегда (сумма — из профиля)
    return who is None or (who == "ip_alone") == ip_alone


def _periods(item: dict, year: int) -> list[tuple[date, date]]:
    if "every" in item:
        step = item["every"]
        return [(date(year, m, 1), _last_day(*_shift_month(year, m, step - 1))) for m in range(1, 13, step)]
    m1, m2 = item["months"]
    return [(date(year, m1, 1), _last_day(year, m2))]


def obligations(segments: list[Segment], date_from: date, date_to: date) -> list[Obligation]:
    """Платежи со сроком в окне [date_from, date_to] — по срокам, затем по названию."""
    out: list[Obligation] = []
    for seg in segments:
        seg_end = seg.end or date.max
        if seg.start > date_to:
            continue
        # срок бывает до 7 месяцев после периода (взносы 1 %): берём периоды с запасом в год
        for year in range(max(seg.start.year, date_from.year - 1), min(seg_end.year, date_to.year) + 1):
            rules = rules_on(date(year, 1, 1))
            for name in schedules(seg, date(year, 1, 1)):
                for item in rules["schedules"].get(name, []):
                    if not _applies(item, seg):
                        continue
                    for full_start, full_end in _periods(item, year):
                        start, end = max(full_start, seg.start), min(full_end, seg_end)
                        if start > end:
                            continue
                        if "due_after_start" in item:
                            due_rule = start + timedelta(days=item["due_after_start"])
                            part = f"d{item['due_after_start']}"
                        else:
                            due_rule = _rule_day(full_end, item["due"])
                            part = str(item["due"][0])
                        due = next_workday(due_rule)
                        if not date_from <= due <= date_to:
                            continue
                        notice = next_workday(_rule_day(full_end, item["notice"])) if "notice" in item else None
                        out.append(Obligation(
                            key=f"tax:{name}:{item['what']}:{start.isoformat()}:{part}",
                            schedule=name, what=item["what"], title=item["title"],
                            period_start=start, period_end=end, full_start=full_start, full_end=full_end,
                            due=due, due_rule=due_rule, notice=notice, doc=item.get("doc"),
                            share=_share(item.get("share")), segment=seg,
                            extra={k: item[k] for k in ("threshold", "rate") if k in item},
                        ))
    return sorted(out, key=lambda o: (o.due, o.title, o.period_start))


def has_deadlines(seg: Segment) -> bool:
    """Есть ли у режима сроки в справочнике (у «своего процента» и ЕСХН без взносов — нет)."""
    return any(
        _applies(item, seg)
        for name in schedules(seg, seg.start)
        for item in rules_on(seg.start)["schedules"].get(name, [])
    )
