"""Калькулятор минимальной и целевой цены (шаг 4.2, docs/BUSINESS_LOGIC.md §4). Чистый модуль — без БД и HTTP.

    P = (C × НДС + L + S + X) / (1 − комиссия − реклама − эквайринг − M)

Часть расходов — доля цены (комиссия, эквайринг, реклама, НДС с продаж, налог с дохода), часть — рубли на штуку
(себестоимость, логистика, возвраты и невыкупы, хранение, прочее). Прибыль с 1 шт. при цене P (до налога на прибыль)
линейна:

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
    extra_pct: Decimal = ZERO  # E — доп. расходы продавца, % от цены (не услуги Ozon: без вычета НДС услуг)
    returns: Decimal = ZERO  # R — возвраты и невыкупы на 1 проданную шт., ₽ (обратная логистика, обработка; 4.2a)

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
    ozon_fixed = p.logistics + p.storage + p.returns
    if t.vat_deduct:  # всё без НДС: НДС поставщика и услуг Ozon — к вычету
        services = HUNDRED / (HUNDRED + t.services_vat_pct)
        alpha = net - shares * services
        beta = ozon_fixed * services + p.other + p.cost
    else:
        alpha = net - shares
        beta = ozon_fixed + p.other + p.cost * p.supplier_vat
    alpha -= net * t.income_tax_pct / HUNDRED
    alpha -= p.extra_pct / HUNDRED  # свои расходы долей цены — не услуги Ozon, НДС с них не вычитается
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
    extra: Decimal = ZERO  # доп. расходы продавца, % от цены → ₽
    returns: Decimal = ZERO  # возвраты и невыкупы на 1 шт. (4.2a)


def breakdown(p: PriceParams, price: Decimal) -> Breakdown:
    t = p.tax
    commission = price * p.commission_pct / HUNDRED
    acquiring = price * p.acquiring_pct / HUNDRED
    ad = price * p.ad_pct / HUNDRED
    extra = price * p.extra_pct / HUNDRED
    cost = p.cost * p.supplier_vat
    out_vat = price * t.vat_pct / (HUNDRED + t.vat_pct)
    vat = out_vat
    if t.vat_deduct:
        services = commission + acquiring + ad + p.logistics + p.storage + p.returns
        vat -= cost - p.cost + services * t.services_vat_pct / (HUNDRED + t.services_vat_pct)
    income_tax = (price - out_vat) * t.income_tax_pct / HUNDRED
    profit = (price - commission - acquiring - ad - extra - p.logistics - p.returns - p.storage - p.other - cost - vat
              - income_tax)
    profit_tax = max(profit, ZERO) * t.profit_tax_pct / HUNDRED

    def q(v: Decimal) -> Decimal:
        return v.quantize(CENT, ROUND_HALF_UP)

    return Breakdown(
        price=q(price), commission=q(commission), acquiring=q(acquiring), ad=q(ad), logistics=q(p.logistics),
        storage=q(p.storage), other=q(p.other), cost=q(cost), vat=q(vat), income_tax=q(income_tax), profit=q(profit),
        margin=q(profit / price * HUNDRED) if price > 0 else None, profit_tax=q(profit_tax), net=q(profit - profit_tax),
        extra=q(extra), returns=q(p.returns),
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


# ── логистика и возвраты на одну продажу (правка 16.11.2026) ──
MIN_TRIPS = 20  # отправок у товара меньше — выкуп смешиваем с выкупом магазина


@dataclass(frozen=True)
class Trips:
    """Отправки за окно продаж по начислениям: продано, невыкуплено (доехал и вернулся), вернули после покупки;
    расходы туда (логистика, последняя миля, упаковка) и обратно (обратная логистика, обработка), ₽ — положительные."""

    sold: int
    not_bought: int
    returned: int
    forward: Decimal
    back: Decimal

    @property
    def trips(self) -> int:  # поездок туда: проданные и невыкупленные
        return self.sold + self.not_bought

    @property
    def backs(self) -> int:  # поездок обратно: невыкупы и возвраты
        return self.not_bought + self.returned


@dataclass(frozen=True)
class PerSale:
    """Логистика и возвраты на 1 проданную шт. = расход на одну отправку × сколько отправок на одну продажу."""

    logistics: Decimal  # ₽ на 1 проданную шт.
    returns: Decimal
    per_trip: Decimal  # туда — за одну отправку
    per_back: Decimal | None  # обратно — за один невыкуп или возврат; None — не было ни у товара, ни у магазина
    per_back_shop: bool  # за один невыкуп взяли средний по магазину (у товара их не было)
    buyout: Decimal  # выкуп в расчёте, доля
    own_buyout: Decimal  # выкуп товара по факту, доля
    shop_buyout: Decimal | None
    smoothed: bool  # отправок мало — выкуп с учётом магазина
    trips_per_sale: Decimal  # = 1 / выкуп
    backs_per_sale: Decimal  # невыкупов и возвратов на одну продажу


def per_sale(product: Trips, shop: Trips | None) -> PerSale:
    """Расходы туда и обратно на одну продажу. У товара MIN_TRIPS отправок и больше — ровно его факт (расходы ÷
    проданные). Меньше — выкуп и доля возвратов смешиваются с магазинными: товар весит своими отправками, магазин —
    недостающими до MIN_TRIPS, — одна-две случайные отмены не раздувают цену. Продаж у товара нет — не вызывается."""
    trips, sold = Decimal(product.trips), Decimal(product.sold)
    own_b = sold / trips
    shop_b = Decimal(shop.sold) / shop.trips if shop and shop.trips else None
    per_trip = product.forward / trips
    per_back_shop = not product.backs
    if product.backs:
        per_back = product.back / product.backs
    else:
        per_back = shop.back / shop.backs if shop and shop.backs else None
    if product.trips >= MIN_TRIPS or shop_b is None:
        b, c, smoothed = own_b, Decimal(product.returned) / trips, False
        logistics, returns = product.forward / sold, product.back / sold  # ровно факт
    else:
        k = Decimal(MIN_TRIPS - product.trips)
        shop_c = Decimal(shop.returned) / shop.trips
        b = (sold + k * shop_b) / MIN_TRIPS
        c = (Decimal(product.returned) + k * shop_c) / MIN_TRIPS
        smoothed = True
        logistics = per_trip / b
        returns = (per_back or ZERO) * (1 - b + c) / b
    return PerSale(
        logistics=logistics.quantize(CENT, ROUND_HALF_UP), returns=returns.quantize(CENT, ROUND_HALF_UP),
        per_trip=per_trip.quantize(CENT, ROUND_HALF_UP),
        per_back=None if per_back is None else per_back.quantize(CENT, ROUND_HALF_UP), per_back_shop=per_back_shop,
        buyout=b, own_buyout=own_b, shop_buyout=shop_b, smoothed=smoothed,
        trips_per_sale=1 / b, backs_per_sale=(1 - b + c) / b,
    )
