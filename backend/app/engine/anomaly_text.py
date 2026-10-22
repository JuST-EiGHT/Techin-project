"""Тексты аномалий (шаг 7.1): заголовок, пояснение «что случилось и что сделать», единица метрики и куда перейти.

Собираются при чтении из того, что записал детектор (тип, метрика до / после, порог, details), — одинаково для страницы
уведомлений (7.2) и Telegram (7.3). Чистый модуль. Ссылки — адреса карты сервиса (шаг 10.1, docs/SITEMAP.md): старые
переадресуются, но новые сообщения сразу ведут на новое место.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.engine import anomaly_detector as det

TYPE_TITLES = {
    det.SALES_DROP: "Падение продаж",
    det.SALES_GROWTH: "Рост продаж",
    det.PRICE_CHANGE: "Изменение цены",
    det.COMMISSION_CHANGE: "Изменение комиссии Ozon",
    det.LOW_MARGIN: "Маржа ниже порога",
    det.LOW_STOCK: "Заканчивается остаток",
    det.PROMO_AUTO_ADDED: "Ozon добавил товар в акцию",
    det.PROMO_LOSS: "Убыток в акции",
    det.PAYOUT_MISSED: "Выплата Ozon не пришла",
    det.PAYOUT_DIFF: "Выплата разошлась с прогнозом",
    det.BANK_MISSING: "Выплата не дошла до счёта",
    det.BANK_DIFF: "На счёт пришло другое",
    det.CASH_GAP: "Кассовый разрыв",
    det.UNCATEGORIZED: "Операции без статьи",
}
UNITS = {
    det.SALES_DROP: "шт./день", det.SALES_GROWTH: "шт./день", det.PRICE_CHANGE: "₽", det.COMMISSION_CHANGE: "%",
    det.LOW_MARGIN: "%", det.LOW_STOCK: "дн.", det.PROMO_AUTO_ADDED: "%", det.PROMO_LOSS: "%", det.PAYOUT_MISSED: "₽",
    det.PAYOUT_DIFF: "₽", det.BANK_MISSING: "₽", det.BANK_DIFF: "₽", det.CASH_GAP: "₽", det.UNCATEGORIZED: "шт.",
}


@dataclass(frozen=True)
class Text:
    title: str
    message: str
    link: str | None


def num(v: Decimal | float | int | None, places: int = 1) -> str:
    if v is None:
        return "—"
    q = Decimal(str(v)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP).normalize()
    text = f"{q:f}".replace("-", "−").replace(".", ",")
    return "0" if text == "−0" else text


def rub(v: Decimal | str | None) -> str:
    if v is None:
        return "—"
    text = f"{Decimal(v).quantize(Decimal('0.01'), ROUND_HALF_UP):,.2f}".replace(",", " ").replace(".", ",")
    return text.removesuffix(",00").replace("-", "−") + " ₽"


def _day(iso: str | None, year: bool = False) -> str:
    if not iso:
        return "—"
    d = date.fromisoformat(iso[:10])
    return d.strftime("%d.%m.%Y" if year else "%d.%m")


def _pct(v) -> str:
    return f"{num(abs(Decimal(str(v))), 1)} %"


def describe(
    type_: str, severity: str, before: Decimal | None, after: Decimal | None, threshold: Decimal | None,
    details: dict | None, product_id=None, product_name: str | None = None, today: date | None = None,
) -> Text:
    d = details or {}
    what = f"«{product_name}»" if product_name else "товара"
    product_link = f"/dashboard/products?product={product_id}" if product_id else None

    if type_ in (det.SALES_DROP, det.SALES_GROWTH):
        shop = product_id is None
        subject = "Продажи магазина" if shop else "Продажи"
        verb = "упали" if type_ == det.SALES_DROP else "выросли"
        title = f"{subject} {verb} на {_pct(d.get('change_pct', 0))}"
        days = d.get("days", 3)
        base = (f"За последние {days} дн. продано {d.get('actual')} шт., а обычно за такие дни — около "
                f"{num(d.get('expected'))} шт. (по 7 дням до них, с поправкой на выходные).")
        if type_ == det.SALES_DROP:
            tip = ("Проверьте цену, остатки, участие в акциях и карточку товара." if not shop else
                   "Посмотрите, у каких товаров упали продажи, — «Прибыль по товарам».")
        else:
            tip = "Проверьте цену: не слишком ли низкая, не попал ли товар в акцию."
        return Text(title, f"{base} {tip}", product_link or "/dashboard/finance/pnl?tab=products")

    if type_ == det.PRICE_CHANGE:
        up = after is not None and before is not None and after > before
        title = f"Цена {'выросла' if up else 'снизилась'} на {_pct(d.get('change_pct', 0))}: {rub(before)} → {rub(after)}"
        who = ("Цену меняли через сервис — подробности в «Истории цен»." if d.get("via_service") else
               "Через сервис цену не меняли: её изменили в кабинете Ozon или это сделал сам Ozon.")
        return Text(title, f"Цена {what} за 7 дней: было {rub(before)}, стало {rub(after)}. {who}",
                    f"/dashboard/products/prices/history?product={product_id}" if d.get("via_service") and product_id else product_link)

    if type_ == det.COMMISSION_CHANGE:
        up = after is not None and before is not None and after > before
        title = (f"Комиссия Ozon {'выросла' if up else 'снизилась'} на {num(abs(Decimal(str(d.get('change_pp', 0)))))} п. п.: "
                 f"{num(before)} % → {num(after)} %")
        return Text(title, f"Комиссия {what} изменилась за 7 дней — вместе с ней маржа и минимальная цена. Проверьте цену "
                           "в разделе «Цены».", product_link)

    if type_ == det.LOW_MARGIN:
        loss = after is not None and after < 0
        title = f"Маржа {num(after)} % — {'каждая продажа в минус' if loss else f'ниже порога {num(threshold)} %'}"
        price = f" {rub(d['price'])}" if d.get("price") else ""
        return Text(title, f"При текущей цене{price} у {what} остаётся меньше, чем вы считаете нормой. Минимальная и "
                           "целевая цена — в разделе «Цены», порог — в «Настройках».", product_link)

    if type_ == det.LOW_STOCK:
        if after is not None and after <= 0:
            title = "Товар закончился"
        else:
            title = f"Остатка на {num(after)} дн."
        avg = d.get("avg_daily")
        return Text(title, f"На складах {d.get('available', 0)} шт. {what}, продаётся {num(avg, 2) if avg else '—'} шт. в день — "
                           f"меньше порога «{'Критично' if severity == det.RED else 'Мало'}» ({num(threshold, 0)} дн.). "
                           "Пора планировать поставку — раздел «Поставки».", product_link)

    if type_ in (det.PROMO_AUTO_ADDED, det.PROMO_LOSS):
        promo = d.get("promotion") or "акция"
        link = f"/dashboard/products/promotions/{d['promotion_id']}" if d.get("promotion_id") else "/dashboard/products/promotions"
        price = f" по цене {rub(d['action_price'])}" if d.get("action_price") else ""
        margin = f"маржа {num(after)} %" if after is not None else "маржу посчитать нечем"
        subject = f"Товар «{product_name}»" if product_name else "Товар"
        if type_ == det.PROMO_AUTO_ADDED:
            return Text(f"Ozon сам добавил товар в акцию «{promo}»",
                        f"{subject} участвует{price}, {margin}. Если акция невыгодна — уберите товар из неё в разделе "
                        "«Акции».", link)
        return Text(f"Маржа в акции «{promo}» {num(after)} %",
                    f"{subject}{price} продаётся в минус с каждой штуки. Уберите товар из акции или проверьте "
                    "себестоимость.", link)

    if type_ == det.PAYOUT_MISSED:
        amount = f" (около {rub(before)})" if before is not None else ""
        return Text(f"Выплата Ozon {_day(d.get('day'))} не пришла",
                    f"По графику выплата ожидалась {_day(d.get('day'), True)}{amount}, но в данных Ozon её нет. Проверьте "
                    "баланс в кабинете Ozon («Финансы → Баланс»).", "/dashboard/finance/payouts")

    if type_ == det.PAYOUT_DIFF:
        more = after is not None and before is not None and after > before
        week = f" (начисления недели продаж с {_day(d.get('week_from'))})" if d.get("week_from") else ""
        return Text(f"Выплата {_day(d.get('day'))} {'больше' if more else 'меньше'} прогноза на {_pct(d.get('change_pct', 0))}",
                    f"Пришло {rub(after)}, по графику ожидали {rub(before)}{week}. Посмотрите начисления за ту неделю: "
                    "возвраты, удержания, хранение.", "/dashboard/finance/payouts")

    if type_ == det.BANK_MISSING:
        return Text(f"Выплата Ozon {_day(d.get('day'))} не дошла до счёта",
                    f"Ozon выплатил {rub(before)}, а в выписке банка поступления нет, хотя выписка за эти дни загружена. "
                    "Проверьте реквизиты для выплат в кабинете Ozon и выписку.", "/dashboard/operations/accounts")

    if type_ == det.BANK_DIFF:
        diff = (Decimal(after) - Decimal(before)) if after is not None and before is not None else None
        return Text(f"Выплата Ozon {_day(d.get('day'))}: на счёт пришло {rub(after)}",
                    f"Ozon выплатил {rub(before)}, в банке {_day(d.get('bank_day'))} — {rub(after)} (разница {rub(diff)}).",
                    "/dashboard/operations/accounts")

    if type_ == det.CASH_GAP:
        if severity == det.RED:
            title = f"Кассовый разрыв {_day(d.get('first_day'))}"
            text = f"По прогнозу остаток на счетах уйдёт ниже нуля {_day(d.get('first_day'), True)}"
        else:
            title = f"Остаток ниже порога с {_day(d.get('first_day'))}"
            text = f"По прогнозу остаток опустится ниже вашего порога {rub(threshold)} {_day(d.get('first_day'), True)}"
        return Text(title, f"{text}; минимум — {rub(after)} {_day(d.get('min_day'))}. Перенесите платежи или "
                           "запланируйте поступления — «Календарь».", "/dashboard/planning/calendar")

    if type_ == det.UNCATEGORIZED:
        frm = d.get("oldest_day")
        to = (today or date.today()).isoformat()
        link = f"/dashboard/operations?category=none&from={frm}&to={to}" if frm else "/dashboard/operations"
        return Text(f"Операций без статьи: {num(after, 0)}",
                    f"Они без статьи дольше {num(threshold, 0)} дн. — ДДС по статьям и прогноз неточны. Разметьте их или "
                    "задайте правило разнесения.", link)

    return Text(TYPE_TITLES.get(type_, type_), "", None)
