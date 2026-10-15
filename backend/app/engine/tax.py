"""Налог и НДС продавца за период по налоговому профилю (шаг 2.5). Чистый модуль — без БД и HTTP.

Правила — docs/TAXES.md; ставки, пороги и лимиты — данные с датой действия (app/data/tax_rules.json).

Главное: доход — полная цена продажи покупателю, а не сумма, которая пришла на счёт. Ozon — посредник:
его комиссия, логистика и другие удержания доход не уменьшают, это расходы — их учитывают режимы
«доходы минус расходы» и ОСН. Расчёт — оценка для планирования денег, а не декларация.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path

from app.integrations.xlsx.common import norm
from app.schemas.report import NormalizedReportData

RULES_FILE = Path(__file__).resolve().parents[1] / "data" / "tax_rules.json"
CENT = Decimal("0.01")
ZERO = Decimal(0)

FORMS = {"ip": "ИП", "ooo": "ООО", "self_employed": "Самозанятый"}
VAT_MODES = {
    "none": "Не плачу НДС",
    "special": "НДС 5 % или 7 % — без вычетов",
    "general": "НДС 22 %, 10 % или 0 % — с вычетом входящего",
}


@dataclass(frozen=True)
class Regime:
    key: str
    title: str
    forms: tuple[str, ...]  # кому доступен
    vat_modes: tuple[str, ...]  # какие режимы НДС возможны
    kind: str  # income — с доходов, profit — с разницы, patent — патент, custom — свой процент
    summary: str  # коротко — в списке выбора
    restriction: str | None = None  # когда режим не подходит продавцу


REGIMES: dict[str, Regime] = {
    r.key: r
    for r in (
        Regime(
            "usn_income",
            "УСН «Доходы»",
            ("ip", "ooo"),
            ("none", "special", "general"),
            "income",
            "6 % с доходов, страховые взносы уменьшают налог. Подходит, когда расходов немного.",
        ),
        Regime(
            "usn_income_expense",
            "УСН «Доходы минус расходы»",
            ("ip", "ooo"),
            ("none", "special", "general"),
            "profit",
            "15 % с разницы доходов и расходов, но не меньше 1 % доходов. Выгоднее, когда расходы — от 60 % выручки.",
        ),
        Regime(
            "osn",
            "ОСН — общая система",
            ("ip", "ooo"),
            ("general",),
            "profit",
            "Налог на прибыль 25 % (ООО) или НДФЛ (ИП) и НДС 22 % или 10 % с вычетом входящего.",
        ),
        Regime(
            "ausn_income",
            "АУСН «Доходы»",
            ("ip", "ooo"),
            ("none",),
            "income",
            "8 % с доходов, без страховых взносов и НДС. Доход до 60 млн ₽ в год, до 5 сотрудников.",
        ),
        Regime(
            "ausn_income_expense",
            "АУСН «Доходы минус расходы»",
            ("ip", "ooo"),
            ("none",),
            "profit",
            "20 % с разницы, но не меньше 3 % доходов. Доход до 60 млн ₽ в год, до 5 сотрудников.",
        ),
        Regime(
            "npd",
            "НПД — самозанятый",
            ("self_employed", "ip"),
            ("none",),
            "income",
            "4 % при продажах физлицам, 6 % — компаниям. Доход до 2,4 млн ₽ в год, без сотрудников.",
            "Только товары собственного производства: перепродавать покупные товары на НПД нельзя.",
        ),
        Regime(
            "psn",
            "ПСН — патент",
            ("ip",),
            ("none",),
            "patent",
            "Фиксированная стоимость патента. Доход до 20 млн ₽ в год, до 15 сотрудников.",
            "Только товары собственного производства по патенту на производство: для перепродажи и с "
            "патентом на торговлю не подходит.",
        ),
        Regime(
            "eshn",
            "ЕСХН",
            ("ip", "ooo"),
            ("none", "general"),
            "profit",
            "6 % с разницы доходов и расходов.",
            "Только для сельхозпроизводителей: при перепродаже товаров не подходит.",
        ),
        Regime(
            "custom",
            "Свой процент",
            ("ip", "ooo", "self_employed"),
            ("none",),
            "custom",
            "Как в калькуляторе: свой процент от прибыли или от дохода — если считаете налог по-своему.",
        ),
    )
}


# ---------- справочник ставок ----------


@cache
def _rules() -> dict:
    return json.loads(RULES_FILE.read_text(encoding="utf-8"))


def _on(entries: list[dict], day: date) -> dict:
    """Запись, действующая на дату: последняя с from ≤ day; для дат раньше первой — первая."""
    ordered = sorted(entries, key=lambda e: e["from"])
    current = ordered[0]
    for entry in ordered:
        if date.fromisoformat(entry["from"]) <= day:
            current = entry
    return current


def _rate_rule(regime: str, legal_form: str | None, day: date) -> dict | None:
    key = f"osn_{'ip' if legal_form == 'ip' else 'ooo'}" if regime == "osn" else regime
    entries = _rules()["rates"].get(key)
    return _on(entries, day) if entries else None


def default_rate(regime: str, legal_form: str | None, day: date) -> Decimal | None:
    """Стандартная ставка режима, %; у «своего процента» и патента — нет."""
    rule = _rate_rule(regime, legal_form, day)
    return Decimal(str(rule["rate"])) if rule else None


def min_rate(regime: str, day: date) -> Decimal | None:
    """Минимальный налог, % от дохода (УСН и АУСН «доходы минус расходы»)."""
    rule = _rate_rule(regime, None, day)
    return Decimal(str(rule["min_rate"])) if rule and "min_rate" in rule else None


def vat_rules(day: date) -> dict:
    """Ставки НДС на дату: general — общие (с вычетом), special — УСН без вычетов, supplier_default."""
    return _on(_rules()["vat"], day)


def limits(day: date) -> dict:
    return _on(_rules()["limits"], day)


def vat_rates(mode: str, day: date) -> list[int]:
    rules = vat_rules(day)
    return {"none": [0], "special": rules["special"], "general": rules["general"]}[mode]


# ---------- профиль и данные периода ----------


@dataclass(frozen=True)
class Profile:
    regime: str
    legal_form: str | None = None  # ip | ooo | self_employed
    rate_pct: Decimal | None = None  # ставка налога, %; None — стандартная на дату
    custom_base: str = "profit"  # для «своего процента»: profit | revenue
    vat_mode: str = "none"  # none | special | general
    vat_rate_pct: Decimal = ZERO
    supplier_vat_pct: Decimal = Decimal(22)  # НДС в цене поставщика по умолчанию
    contributions_year: Decimal | None = None  # страховые взносы за год (ИП за себя и за сотрудников), ₽
    has_employees: bool = False
    patent_cost_year: Decimal | None = None  # ПСН: стоимость патента за год, ₽

    def rate(self, day: date) -> Decimal:
        rate = self.rate_pct if self.rate_pct is not None else default_rate(self.regime, self.legal_form, day)
        return rate if rate is not None else ZERO


@dataclass(frozen=True)
class TaxInput:
    """Суммы за период. Доход — с НДС, если продавец его платит: НДС входит в цену продажи."""

    income: Decimal  # полная цена продаж за вычетом возвратов + выплаты и компенсации Ozon
    ozon_expenses: Decimal  # удержания Ozon: вознаграждение, логистика, хранение, продвижение… (с НДС в них)
    cost_sales: Decimal  # себестоимость проданного без НДС
    cost_with_vat: Decimal  # она же с НДС поставщика
    payout: Decimal  # пришло (придёт) от Ozon — для «своего процента от прибыли»
    revenue_base: Decimal  # выручка — для «своего процента от дохода»
    days: int = 30  # дней в периоде — для годовых взносов и патента
    ozon_expenses_known: bool = True  # в отчёте о реализации удержаний Ozon нет
    income_note: str = ""  # из чего сложился доход — в пояснение


@dataclass
class TaxResult:
    regime: str
    tax: Decimal
    vat: Decimal
    base: Decimal | None  # налоговая база: доход или доход − расходы; у патента — нет
    lines: list[str] = field(default_factory=list)  # расчёт по шагам, с цифрами
    warnings: list[str] = field(default_factory=list)
    tax_exact: Decimal = ZERO  # без округления — для P&L: округляем один раз, в конце (как калькулятор)
    vat_exact: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.tax + self.vat


def _result(
    profile: "Profile", tax: Decimal, vat: Decimal, base: Decimal | None, lines: list[str], warnings: list[str]
) -> TaxResult:
    return TaxResult(
        profile.regime,
        _money(tax),
        _money(vat),
        None if base is None else _money(base),
        lines,
        warnings,
        tax_exact=tax,
        vat_exact=vat,
    )


def uses_costs(profile: Profile) -> bool:
    """Влияет ли себестоимость на налог: режимы «доходы минус расходы», ОСН, ЕСХН, свой процент от прибыли."""
    kind = REGIMES[profile.regime].kind
    return kind == "profit" or (kind == "custom" and profile.custom_base != "revenue")


def rub(value: Decimal) -> str:
    text = f"{Decimal(value).quantize(CENT, ROUND_HALF_UP):,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} ₽"


def num(value: Decimal) -> str:
    return f"{Decimal(value).normalize():f}".replace(".", ",")


def pct(value: Decimal) -> str:
    return f"{num(value)} %"


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


# ---------- доход и расходы из отчёта Ozon ----------

INCOME_RETURNS = ("возврат выручки",)  # уменьшают доход, а не идут в расходы


def split_accruals(by_type: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
    """Начисления по типам → (доход, удержания Ozon). Доход — «Выручка», «Баллы за скидки», выплаты
    партнёров и другие поступления минус «Возврат выручки»; удержания — остальные списания минус
    их возвраты («Возврат вознаграждения»). Доход − удержания = сколько пришло от Ozon."""
    income = expenses = ZERO
    for name, amount in by_type.items():
        text = norm(name)
        amount = Decimal(amount)
        if any(marker in text for marker in INCOME_RETURNS):
            income += amount
        elif amount > 0 and "возврат" not in text:
            income += amount
        else:
            expenses -= amount
    return income, expenses


def report_tax_input(data: NormalizedReportData, cost_with_vat: Decimal, payout: Decimal | None = None) -> TaxInput:
    """Суммы отчёта для расчёта налога. payout — «сколько реально пришло», если пользователь его поправил."""
    days = (data.period_end - data.period_start).days + 1 if data.period_start and data.period_end else 30
    if data.report_type == "accrual":
        income, expenses = split_accruals(data.by_type)
        note, known = "выручка, баллы за скидки и выплаты партнёров за вычетом возвратов", True
    else:  # в отчёте о реализации — продажи по цене для покупателя, удержаний Ozon в нём нет
        income, expenses = data.auto_total, ZERO
        note, known = "«Итого реализовано» и выплаты партнёров", False
    return TaxInput(
        income=income,
        ozon_expenses=expenses,
        cost_sales=data.cost_sales,
        cost_with_vat=cost_with_vat,
        payout=data.auto_total if payout is None else payout,
        revenue_base=data.revenue_base,
        days=days,
        ozon_expenses_known=known,
        income_note=note,
    )


# ---------- расчёт ----------


def compute_tax(profile: Profile, data: TaxInput, day: date) -> TaxResult:
    """Налог и НДС за период по профилю, действующему на дату day. Каждый шаг — строкой в lines."""
    regime = REGIMES[profile.regime]
    rate = profile.rate(day)
    lines: list[str] = []
    warnings: list[str] = []

    if regime.kind == "custom":
        if profile.custom_base == "revenue":
            base = data.revenue_base
            lines.append(f"Выручка: {rub(base)}")
        else:
            base = data.payout - data.cost_with_vat
            lines.append(
                f"Прибыль: пришло от Ozon {rub(data.payout)} − себестоимость с НДС {rub(data.cost_with_vat)} = "
                f"{rub(base)}"
            )
        tax = max(base, ZERO) * rate / 100
        lines.append(f"Налог {pct(rate)}: {rub(max(base, ZERO))} × {pct(rate)} = {rub(tax)}")
        return _result(profile, tax, ZERO, base, lines, warnings)

    # НДС: входит в цену продажи; доход для налога — без него
    vat_pct = profile.vat_rate_pct if profile.vat_mode != "none" else ZERO
    deduct = profile.vat_mode == "general"
    general = Decimal(vat_rules(day)["general"][0])
    income = data.income
    lines.append(f"Доход: {rub(income)}" + (f" — {data.income_note}" if data.income_note else ""))
    out_vat = income * vat_pct / (100 + vat_pct)
    ozon_vat = data.ozon_expenses * general / (100 + general) if deduct else ZERO
    supplier_vat = data.cost_with_vat - data.cost_sales if deduct else ZERO
    vat = ZERO
    income_net = income - out_vat
    if profile.vat_mode != "none":
        lines.append(
            f"НДС {pct(vat_pct)} в цене продаж: {rub(income)} × {num(vat_pct)}/{num(100 + vat_pct)} = {rub(out_vat)}"
        )
        vat = out_vat
        if deduct:
            lines.append(
                f"К вычету: НДС поставщика {rub(supplier_vat)} и НДС в услугах Ozon {rub(ozon_vat)} "
                f"({rub(data.ozon_expenses)} × {num(general)}/{num(100 + general)})"
            )
            vat = out_vat - supplier_vat - ozon_vat
            if vat < 0:
                warnings.append(
                    f"НДС к вычету больше начисленного на {rub(-vat)} — налог за этот период он не уменьшает."
                )
                vat = ZERO
        lines.append(f"НДС к уплате: {rub(vat)}")
        lines.append(f"Доход без НДС: {rub(income_net)}")

    contributions = (profile.contributions_year or ZERO) * data.days / 365
    contributions_text = (
        f"страховые взносы за {data.days} дн. ({rub(profile.contributions_year)} в год × {data.days}/365)"
        if profile.contributions_year
        else ""
    )

    if regime.kind == "patent":
        cost = profile.patent_cost_year or ZERO
        tax = cost * data.days / 365
        lines.append(f"Патент: {rub(cost)} в год × {data.days}/365 = {rub(tax)}")
        return _result(profile, tax, ZERO, None, lines, warnings)

    if regime.kind == "income":
        base = income_net
        tax = base * rate / 100
        lines.append(f"Налог {pct(rate)}: {rub(base)} × {pct(rate)} = {rub(tax)}")
        if profile.regime == "usn_income" and contributions > 0:
            full = profile.legal_form == "ip" and not profile.has_employees
            cap = tax if full else tax / 2
            reduction = min(contributions, cap)
            tax -= reduction
            limit = "" if full else "; с сотрудниками и у ООО — не больше половины налога"
            lines.append(f"Минус {contributions_text}: −{rub(reduction)}{limit}")
            lines.append(f"Налог к уплате: {rub(tax)}")
        return _result(profile, tax, vat, base, lines, warnings)

    # profit: доход − расходы
    ozon_net = data.ozon_expenses - ozon_vat
    cogs = data.cost_sales if deduct else data.cost_with_vat
    parts = [
        f"удержания Ozon{' без НДС' if deduct else ''} {rub(ozon_net)}",
        f"себестоимость {'без НДС' if deduct else 'с НДС поставщика'} {rub(cogs)}",
    ]
    expenses = ozon_net + cogs
    if contributions > 0 and profile.regime in ("usn_income_expense", "osn"):
        expenses += contributions
        parts.append(f"{contributions_text} {rub(contributions)}")
    base = income_net - expenses
    lines.append(f"Расходы: {' + '.join(parts)} = {rub(expenses)}")
    lines.append(f"База: {rub(income_net)} − {rub(expenses)} = {rub(base)}")
    tax = max(base, ZERO) * rate / 100
    lines.append(f"Налог {pct(rate)}: {rub(max(base, ZERO))} × {pct(rate)} = {rub(tax)}")
    if (minimum := min_rate(profile.regime, day)) is not None:
        min_tax = income_net * minimum / 100
        if min_tax > tax:
            lines.append(
                f"Это меньше минимального налога {pct(minimum)} от дохода ({rub(min_tax)}) — берём минимальный. "
                "По закону он считается за год, здесь — оценка с запасом."
            )
            tax = min_tax
    if not data.ozon_expenses_known:
        warnings.append(
            "В отчёте о реализации нет удержаний Ozon (вознаграждение, логистика) — они в отчёте по начислениям. "
            "Здесь в расходах только себестоимость, поэтому налог завышен."
        )
    return _result(profile, tax, vat, base, lines, warnings)


# ---------- как считаем — для подсказки «?» ----------

INCOME_RULE = (
    "Доход — полная цена продажи покупателю, а не сумма, которая пришла на счёт: Ozon — посредник, его "
    "вознаграждение, логистика и другие удержания доход не уменьшают."
)
INCOME_SOURCES = (
    "В отчёте по начислениям доход = «Выручка» + «Баллы за скидки» + выплаты партнёров − «Возврат выручки»; "
    "в отчёте о реализации — «Итого реализовано» + «Всего выплат от партнёров»."
)
EXPENSES_RULE = (
    "Расходы — удержания Ozon (вознаграждение, логистика, хранение, продвижение, эквайринг и др.) и "
    "себестоимость проданного."
)


def how(profile: Profile, day: date) -> list[str]:
    """Как именно считается налог при этом профиле — словами, без цифр периода."""
    regime = REGIMES[profile.regime]
    rate = profile.rate(day)
    if regime.kind == "custom":
        mine = pct(rate) if profile.rate_pct else "ваш процент"
        if profile.custom_base == "revenue":
            return [f"Налог = выручка × {mine}.", "НДС не считаем."]
        return [
            f"Налог = (пришло от Ozon − себестоимость с НДС поставщика) × {mine}; при убытке — 0.",
            "Так считал HTML-калькулятор. Это не налоговый режим: ставка и база — ваши.",
            "НДС не считаем.",
        ]
    out = [INCOME_RULE, INCOME_SOURCES]
    deduct = profile.vat_mode == "general"
    cogs = "себестоимость без НДС (НДС поставщика — к вычету)" if deduct else "себестоимость с НДС поставщика"
    if regime.kind == "income":
        out.append(f"Налог = доход × {pct(rate)}. Расходы налог не уменьшают.")
        if profile.regime == "usn_income":
            out.append(
                "Страховые взносы уменьшают налог: ИП без сотрудников — до 100 %, с сотрудниками и ООО — до 50 % "
                "налога. Годовые взносы делим на дни периода."
            )
        if profile.regime == "npd":
            out.append("Ставка 4 % — при продажах физлицам; если покупатели — компании, укажите 6 %.")
    elif regime.kind == "patent":
        out = ["Налог — стоимость патента за год, поделённая на дни периода. Доход и расходы на него не влияют."]
    else:
        out.append(EXPENSES_RULE)
        out.append(f"Налог = (доход − удержания Ozon − {cogs}) × {pct(rate)}; при убытке — 0.")
        if (minimum := min_rate(profile.regime, day)) is not None:
            out.append(
                f"Если это меньше {pct(minimum)} от дохода — берём {pct(minimum)} (минимальный налог; по закону он "
                "считается за год, в отчёте за период — оценка с запасом)."
            )
        if profile.regime in ("usn_income_expense", "osn"):
            out.append("Страховые взносы, если указаны, входят в расходы (годовые делим на дни периода).")
        if profile.regime == "osn" and profile.legal_form == "ip":
            out.append("У ИП на ОСН это НДФЛ: шкала прогрессивная — укажите свою ставку.")
    if profile.vat_mode == "special":
        out.append(
            f"НДС {pct(profile.vat_rate_pct)} входит в цену продажи: НДС = доход × "
            f"{num(profile.vat_rate_pct)}/{num(100 + profile.vat_rate_pct)}. Вычетов нет: НДС поставщика и услуг Ozon "
            "остаётся в расходах. Доход для налога — без НДС."
        )
    elif deduct:
        general = vat_rules(day)["general"][0]
        out.append(
            f"НДС {pct(profile.vat_rate_pct)} входит в цену продажи; к уплате = НДС с продаж − НДС поставщика − "
            f"НДС в услугах Ozon ({general}/{100 + general} от удержаний). Доход и расходы для налога — без НДС."
        )
    elif regime.key.startswith("usn"):
        limit = limits(day)["usn_vat_free_income"]
        out.append(
            f"НДС не платите. Если доход за год превысит {limit // 1_000_000} млн ₽, на УСН появится НДС — "
            "укажите его здесь с нужной даты."
        )
    out.append("Это оценка для планирования денег. Декларацию сверяйте с бухгалтером.")
    return out
