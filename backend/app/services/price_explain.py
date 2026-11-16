"""«?» у строк разбора цены (правка владельца 16.11.2026): как посчитана каждая строка — с цифрами товара.

Вместо подписей под строками — одна подсказка у строки. Тексты собираются здесь, по тем же входным данным, что и расчёт
(`services/pricing.PricingContext.inputs`), — цифры в подсказке и в строке одни и те же.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.engine import price_calculator as calc
from app.engine.anomaly_text import plural
from app.engine.tax import num, pct, rub

if TYPE_CHECKING:
    from app.services.pricing import Inputs, PricingContext

YOURS, SAVED, FOR_ALL = "ваше значение", "сохранено для товара", "для всех товаров"


def _p(value: Decimal, places: str = "0.01") -> str:
    return pct(Decimal(value).quantize(Decimal(places), ROUND_HALF_UP))


def _n(value: Decimal) -> str:
    return num(Decimal(value).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _share(share: Decimal) -> str:
    """Доля 0..1 → «68 %»."""
    return pct((share * 100).quantize(Decimal("0.1"), ROUND_HALF_UP))


def _own(source: str | None) -> str | None:
    """Своё значение вместо данных: поле расчёта, сохранённое у товара или для всех товаров."""
    return {YOURS: "ваше значение для этого расчёта", SAVED: "своё значение у товара",
            FOR_ALL: "для всех товаров («Настройки → Цены»)"}.get(source or "")


def _of_price(price: Decimal, share: Decimal, amount: Decimal) -> str:
    return f"{rub(price)} × {_p(share)} = {rub(amount)}."


def _logistics(ctx: PricingContext, inputs: Inputs, amount: Decimal) -> str:
    lg, f = inputs.logistics, inputs.facts
    if lg.source == "manual":
        return f"Ваше значение для этого расчёта: {rub(amount)} на 1 шт."
    if lg.source == "shop":
        return (f"Товар не продавался 90 дней — средняя логистика магазина за {lg.days} дн.: {rub(amount)} на проданную "
                "штуку, оценка.")
    if f is None or f.per_sale is None or f.trips is None:
        return f"По начислениям Ozon: {rub(amount)} на проданную штуку."
    ps, t = f.per_sale, f.trips
    head = (f"Доставка до покупателя за {f.days} дн. (логистика, последняя миля, упаковка): {rub(t.forward)} за "
            f"{t.trips} {plural(t.trips, 'отправку', 'отправки', 'отправок')} — {rub(ps.per_trip)} за одну.")
    return f"{head} {_buyout(ps, t)}: {_n(ps.trips_per_sale)} отправки на одну продажу. " \
           f"{rub(ps.per_trip)} × {_n(ps.trips_per_sale)} ≈ {rub(amount)}."


def _buyout(ps: calc.PerSale, t: calc.Trips) -> str:
    own = f"Выкуп {_share(ps.own_buyout)} ({t.sold} из {t.trips})"
    if ps.smoothed and ps.shop_buyout is not None:
        return (f"{own} — отправок меньше {calc.MIN_TRIPS}, поэтому в расчёте {_share(ps.buyout)} с учётом магазина "
                f"({_share(ps.shop_buyout)})")
    return own


def _returns(ctx: PricingContext, inputs: Inputs, amount: Decimal) -> str:
    f, src = inputs.facts, inputs.sources.get("returns")
    if src == YOURS:
        return f"Ваше значение для этого расчёта: {rub(amount)} на 1 шт."
    if f is None or f.per_sale is None or f.trips is None:
        shop = ctx.shop_facts
        if shop.returns is None:
            return "Данных о невыкупах и возвратах нет — 0 ₽."
        return (f"Товар не продавался 90 дней — средние невыкупы и возвраты магазина за {shop.days} дн.: {rub(amount)} "
                "на проданную штуку, оценка.")
    ps, t = f.per_sale, f.trips
    if ps.per_back is None or (not ps.smoothed and not t.backs):
        return f"За {f.days} дн. невыкупов и возвратов не было ({t.sold} из {t.trips} выкуплено) — {rub(amount)}."
    if ps.per_back_shop:
        one = f"У товара невыкупов за {f.days} дн. не было — один невыкуп или возврат берём средним по магазину: " \
              f"{rub(ps.per_back)}."
    else:
        one = (f"Обратная логистика и обработка за {f.days} дн.: {rub(t.back)} за {t.backs} "
               f"{plural(t.backs, 'невыкуп или возврат', 'невыкупа или возврата', 'невыкупов или возвратов')} — "
               f"{rub(ps.per_back)} за один.")
    return (f"{one} {_buyout(ps, t)}: {_n(ps.backs_per_sale)} невыкупа или возврата на одну продажу. "
            f"{rub(ps.per_back)} × {_n(ps.backs_per_sale)} ≈ {rub(amount)}.")


def _storage(ctx: PricingContext, inputs: Inputs, amount: Decimal) -> str:
    src, f = inputs.sources.get("storage") or "", inputs.facts
    if own := _own(src):
        return f"Хранение на 1 шт. — {own}: {rub(amount)}."
    if src.startswith("по отчёту") and f is not None and f.storage_total is not None:
        return (f"По отчёту Ozon «Стоимость размещения по товарам»: хранение товара за {f.days} дн. — "
                f"{rub(f.storage_total)} ÷ {f.qty} {plural(f.qty, 'проданную штуку', 'проданные штуки', 'проданных штук')} "
                f"= {rub(amount)}.")
    if src.startswith("среднее"):
        why = "товар не продавался" if f is None else "отчёта Ozon о размещении за эти дни нет"
        return f"Оценка: {why} — всё хранение магазина за {ctx.shop_facts.days} дн. ÷ проданные штуки = {rub(amount)}."
    return "Данных о хранении нет — 0 ₽."


def _ad(ctx: PricingContext, inputs: Inputs, price: Decimal, amount: Decimal) -> str:
    share, src, f = inputs.ad_pct, inputs.sources.get("ad_pct") or "", inputs.facts
    calc_text = _of_price(price, share, amount)
    if own := _own(src):
        return f"Реклама — {own}, вместо начислений Ozon: {calc_text}"
    if f is not None and inputs.ad_own_pct is not None and inputs.ad_shared_pct is not None:
        return (f"По начислениям за {f.days} дн.: реклама товара («Звёздные товары» и др.) — {_p(inputs.ad_own_pct)} его "
                f"продаж, реклама без товара (оплата за клик и за заказ — Ozon начисляет её по кампании) — "
                f"{_p(inputs.ad_shared_pct)} продаж магазина, делим по доле в продажах. {calc_text}")
    if src.startswith("средняя"):
        return f"Товар не продавался — вся реклама магазина за {ctx.shop_facts.days} дн. ÷ его продажи. {calc_text}"
    return f"Данных о продажах ещё нет — настройка «Реклама, % — если данных Ozon нет». {calc_text}"


def explain(ctx: PricingContext, inputs: Inputs, b: calc.Breakdown) -> dict[str, str]:
    """Подсказки к строкам разбора цены при цене b.price; строк с нулём нет — подсказок к ним тоже."""
    price, t = b.price, ctx.tax
    out: dict[str, str] = {
        "price": "Ваша цена для расчёта." if inputs.sources.get("price") == YOURS else "Текущая цена товара в Ozon.",
        "commission": (f"Комиссия Ozon {_p(inputs.commission_pct)} от цены — "
                       f"{_own(inputs.sources.get('commission_pct')) or 'из Ozon по товару (FBO)'}. "
                       f"{_of_price(price, inputs.commission_pct, b.commission)}"),
        "logistics": _logistics(ctx, inputs, b.logistics),
        "returns": _returns(ctx, inputs, b.returns),
        "cost": (f"Себестоимость из справочника: {rub(inputs.cost)} без НДС × {num(inputs.supplier_vat)} (НДС "
                 f"поставщика) = {rub(b.cost)}." if inputs.sources.get("cost") != YOURS
                 else f"Ваша себестоимость для расчёта: {rub(inputs.cost)} × {num(inputs.supplier_vat)} = {rub(b.cost)}."),
        "profit": (f"Цена минус все строки выше — {rub(b.profit)}, маржа {_p(b.margin or 0)} от цены."
                   + (f" Налог на прибыль {pct(t.profit_tax_pct)} — после него {rub(b.net)}." if b.profit_tax > 0 else "")),
    }
    if b.acquiring:
        out["acquiring"] = (f"Эквайринг {_p(inputs.acquiring_pct)} от цены — Ozon сообщает его в рублях при текущей цене. "
                            f"{_of_price(price, inputs.acquiring_pct, b.acquiring)}")
    if b.ad:
        out["ad"] = _ad(ctx, inputs, price, b.ad)
    if b.extra:
        out["extra"] = (f"Доп. расходы {_p(inputs.extra_pct)} от цены — "
                        f"{_own(inputs.sources.get('extra_pct')) or 'ваша настройка'}. "
                        f"{_of_price(price, inputs.extra_pct, b.extra)}")
    if b.storage:
        out["storage"] = _storage(ctx, inputs, b.storage)
    if b.other:
        out["other"] = f"Прочие расходы на 1 шт. — {_own(inputs.sources.get('other')) or 'ваши'}: {rub(b.other)}."
    if b.vat:
        out["vat"] = (f"НДС с продаж {pct(t.vat_pct)} входит в цену"
                      + (": к уплате — за вычетом НДС поставщика и НДС в услугах Ozon" if t.vat_deduct else "")
                      + f". {rub(b.vat)}.")
    if b.income_tax:
        out["income_tax"] = (f"{t.title}: {pct(t.income_tax_pct)} от выручки без НДС — {rub(b.income_tax)}.")
    return out
