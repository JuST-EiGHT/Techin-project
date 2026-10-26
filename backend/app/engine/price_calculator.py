"""Калькулятор минимальной и целевой цены (шаг 4.2, docs/BUSINESS_LOGIC.md §4). Чистый модуль — без БД и HTTP.

    P = (C × НДС + L + S + X) / (1 − комиссия − реклама − эквайринг − M)

Часть расходов — доля цены (комиссия, эквайринг, реклама, НДС с продаж, налог с дохода), часть — рубли на штуку
(себестоимость, логистика, хранение, прочее). Прибыль с 1 шт. при цене P (до налога на прибыль) линейна:

    π(P) = α × P − β
    α — что остаётся от рубля цены после долей;  β — расходы на штуку в рублях
    минимальная цена — π = 0:      P = β / α
    целевая цена     — π = M × P:  P = β / (α − M)

Налог и НДС — по налоговому профилю (шаг 2.5, engine/tax.py), как в P&L:
- НДС входит в цену: с продаж — P × v / (100 + v). Без вычетов («special») он целиком расход. С вычетом
  («general») к уплате — минус НДС поставщика и НДС в услугах Ozon: тогда всё считается без НДС.
- налог с дохода (УСН «Доходы», АУСН «Доходы», НПД, свой процент от выручки) — доля дохода без НДС, в знаменателе;
- налог с прибыли (УСН «Д−Р», ОСН, ЕСХН, свой процент от прибыли) на минимальную цену не влияет (прибыль 0); целевая
  маржа — до него, как маржа в «Прибыли по товарам». Сколько останется после него — отдельной строкой;
- патент и страховые взносы — суммы в год, на цену штуки не делим (цена — с запасом).
Знаменатель ≤ 0 — цена недостижима: понятная ошибка, а не бесконечность.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal

from app.engine.tax import REGIMES, Profile, min_rate, pct, vat_rules

CENT = Decimal("0.01")
ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class UnitTax:
    """Что налоговый профиль значит для цены одной штуки."""

    title: str = "Налог не задан"
    known: bool = False  # налоговый профиль задан
    vat_pct: Decimal = ZERO  # НДС с продаж, % — входит в цену
    vat_deduct: bool = False  # с вычетом НДС поставщика и услуг Ozon
    services_vat_pct: Decimal = Decimal(22)  # НДС в услугах Ozon — к вычету
    income_tax_pct: Decimal = ZERO  # налог с дохода без НДС, % — доля в знаменателе
    profit_tax_pct: Decimal = ZERO  # налог с прибыли, % — в цену не входит
    notes: tuple[str, ...] = ()


def unit_tax(profile: Profile | None, day: date) -> UnitTax:
    if profile is None:
        return UnitTax(notes=("Налоговый профиль не задан — налог и НДС в цене не учтены.",))
    regime = REGIMES[profile.regime]
    rate = profile.rate(day)
    notes: list[str] = []
    income = profit = ZERO
    if regime.kind == "income" or (regime.kind == "custom" and profile.custom_base == "revenue"):
        income = rate
        if profile.contributions_year and profile.regime == "usn_income":
            notes.append("Страховые взносы уменьшают налог за год — в цене штуки не учтены, цена с запасом.")
    elif regime.kind == "patent":
        notes.append("Патент — сумма за год, от цены не зависит: в цену штуки не входит.")
    else:
        profit = rate
        if min_rate(profile.regime, day) is not None:
            notes.append(
                f"Минимальный налог {pct(min_rate(profile.regime, day))} от дохода считается за год, а не по штуке — "
                "здесь не учтён."
            )
    vat = profile.vat_rate_pct if profile.vat_mode != "none" else ZERO
    title = regime.title + (f" {pct(rate)}" if regime.kind != "patent" else "")
    if vat > 0:
        title += f", НДС {pct(vat)}" + (" с вычетом" if profile.vat_mode == "general" else "")
    return UnitTax(
        title=title, known=True, vat_pct=vat, vat_deduct=profile.vat_mode == "general",
        services_vat_pct=Decimal(vat_rules(day)["general"][0]), income_tax_pct=income, profit_tax_pct=profit,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class PriceParams:
    cost: Decimal  # C — себестоимость 1 шт. без НДС
    supplier_vat: Decimal  # множитель НДС поставщика: 1.22, без НДС — 1
    commission_pct: Decimal  # комиссия Ozon, % от цены
    acquiring_pct: Decimal = ZERO
    ad_pct: Decimal = ZERO  # реклама, % от цены
    logistics: Decimal = ZERO  # L — логистика на 1 шт., ₽
    storage: Decimal = ZERO  # S — хранение на 1 шт., ₽
    other: Decimal = ZERO  # X — прочие расходы на 1 шт., ₽ (упаковка, маркировка…)
    target_margin_pct: Decimal | None = None  # M — желаемая маржа, % от цены
    tax: UnitTax = field(default_factory=UnitTax)

    @property
    def shares_pct(self) -> Decimal:
        """Услуги Ozon, которые берутся долей цены: комиссия, эквайринг, реклама."""
        return self.commission_pct + self.acquiring_pct + self.ad_pct


class PriceUnreachable(Exception):
    """Знаменатель формулы ≤ 0. Текст — для пользователя."""


def coefficients(p: PriceParams) -> tuple[Decimal, Decimal]:
    """π(P) = α × P − β: прибыль с 1 шт. до налога на прибыль."""
    t = p.tax
    net = HUNDRED / (HUNDRED + t.vat_pct)  # доля цены без НДС с продаж
    shares = p.shares_pct / HUNDRED
    ozon_fixed = p.logistics + p.storage
    if t.vat_deduct:  # всё без НДС: НДС поставщика и услуг Ozon — к вычету
        services = HUNDRED / (HUNDRED + t.services_vat_pct)
        alpha = net - shares * services
        beta = ozon_fixed * services + p.other + p.cost
    else:
        alpha = net - shares
        beta = ozon_fixed + p.other + p.cost * p.supplier_vat
    alpha -= net * t.income_tax_pct / HUNDRED
    return alpha, beta


def _taken_pct(alpha: Decimal) -> str:
    return pct(((1 - alpha) * HUNDRED).quantize(Decimal("0.1"), ROUND_HALF_UP))


def price_for_margin(p: PriceParams, margin_pct: Decimal) -> Decimal:
    """Цена, при которой маржа (до налога на прибыль) равна margin_pct. Округление вверх до копейки:
    по этой цене маржа не меньше заданной."""
    alpha, beta = coefficients(p)
    if alpha <= 0:
        raise PriceUnreachable(
            f"Цена недостижима: комиссия, эквайринг, реклама и налоги с выручки забирают {_taken_pct(alpha)} цены — "
            "при любой цене товар в минус."
        )
    denominator = alpha - margin_pct / HUNDRED
    if denominator <= 0:
        left = (alpha * HUNDRED).quantize(Decimal("0.1"), ROUND_DOWN)
        raise PriceUnreachable(
            f"Маржа {pct(margin_pct)} недостижима: после комиссии, эквайринга, рекламы и налогов с выручки от цены "
            f"остаётся {pct(left)} — маржа должна быть меньше."
        )
    return max((beta / denominator).quantize(CENT, ROUND_CEILING), ZERO)


def price_for_profit(p: PriceParams, profit: Decimal) -> Decimal:
    """Цена, при которой с одной штуки остаётся profit ₽ (до налога на прибыль): π(P) = α × P − β = profit. Округление
    вверх до копейки (шаг 10.26: «минимальная допустимая прибыль»)."""
    alpha, beta = coefficients(p)
    if alpha <= 0:
        raise PriceUnreachable(
            f"Цена недостижима: комиссия, эквайринг, реклама и налоги с выручки забирают {_taken_pct(alpha)} цены — "
            "при любой цене товар в минус."
        )
    return max(((beta + profit) / alpha).quantize(CENT, ROUND_CEILING), ZERO)


@dataclass
class Breakdown:
    """Из чего складывается цена одной штуки: суммы в рублях, расходы — положительные."""

    price: Decimal
    commission: Decimal
    acquiring: Decimal
    ad: Decimal
    logistics: Decimal
    storage: Decimal
    other: Decimal
    cost: Decimal  # себестоимость с НДС поставщика — сколько платим
    vat: Decimal  # НДС к уплате (с вычетами, если они есть)
    income_tax: Decimal
    profit: Decimal  # до налога на прибыль
    margin: Decimal | None  # % от цены
    profit_tax: Decimal  # налог на прибыль с этой суммы — оценка
    net: Decimal  # останется после налога на прибыль


def breakdown(p: PriceParams, price: Decimal) -> Breakdown:
    t = p.tax
    commission = price * p.commission_pct / HUNDRED
    acquiring = price * p.acquiring_pct / HUNDRED
    ad = price * p.ad_pct / HUNDRED
    cost = p.cost * p.supplier_vat
    out_vat = price * t.vat_pct / (HUNDRED + t.vat_pct)
    vat = out_vat
    if t.vat_deduct:
        services = commission + acquiring + ad + p.logistics + p.storage
        vat -= cost - p.cost + services * t.services_vat_pct / (HUNDRED + t.services_vat_pct)
    income_tax = (price - out_vat) * t.income_tax_pct / HUNDRED
    profit = price - commission - acquiring - ad - p.logistics - p.storage - p.other - cost - vat - income_tax
    profit_tax = max(profit, ZERO) * t.profit_tax_pct / HUNDRED

    def q(v: Decimal) -> Decimal:
        return v.quantize(CENT, ROUND_HALF_UP)

    return Breakdown(
        price=q(price), commission=q(commission), acquiring=q(acquiring), ad=q(ad), logistics=q(p.logistics),
        storage=q(p.storage), other=q(p.other), cost=q(cost), vat=q(vat), income_tax=q(income_tax), profit=q(profit),
        margin=q(profit / price * HUNDRED) if price > 0 else None, profit_tax=q(profit_tax), net=q(profit - profit_tax),
    )


def margin_at(p: PriceParams, price: Decimal | None) -> Decimal | None:
    """Маржа при цене, % (до налога на прибыль). Цены нет — None."""
    if price is None or price <= 0:
        return None
    return breakdown(p, price).margin


@dataclass
class PriceResult:
    min_price: Decimal | None
    min_error: str | None
    target_price: Decimal | None
    target_error: str | None
    current: Breakdown | None  # при текущей цене
    promo: Breakdown | None  # при цене в акции
    profitable: bool | None  # текущая цена не ниже минимальной; цены нет — None
    floor_price: Decimal | None = None  # цена с минимальной допустимой прибылью (настройка; 0 — не задана)
    floor_ok: bool | None = None  # текущая цена не ниже неё


def calculate(p: PriceParams, price: Decimal | None = None, promo_price: Decimal | None = None,
              min_profit: Decimal = ZERO) -> PriceResult:
    try:
        min_price, min_error = price_for_margin(p, ZERO), None
    except PriceUnreachable as exc:
        min_price, min_error = None, str(exc)
    target_price = target_error = None
    if p.target_margin_pct is not None:
        try:
            target_price = price_for_margin(p, p.target_margin_pct)
        except PriceUnreachable as exc:
            target_error = str(exc)
    current = breakdown(p, price) if price is not None and price > 0 else None
    promo = breakdown(p, promo_price) if promo_price is not None and promo_price > 0 else None
    floor = None
    if min_profit > 0:
        try:
            floor = price_for_profit(p, min_profit)
        except PriceUnreachable:
            floor = None  # недостижима — как и минимальная: её ошибка уже в min_error
    return PriceResult(
        min_price=min_price, min_error=min_error, target_price=target_price, target_error=target_error,
        current=current, promo=promo, profitable=None if current is None else current.profit >= 0,
        floor_price=floor, floor_ok=None if floor is None or current is None else current.profit >= min_profit,
    )
