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
    det.EXCESS_STOCK: "Избыток запаса",
    det.SYNC_PROBLEM: "Данные не загружаются",
    det.ACCRUAL_GAPS: "Пропуски в начислениях",
    det.REPORT_ERROR: "Отчёт с ошибкой",
    det.MISSING_COST: "Продажи без себестоимости",
}
UNITS = {
    det.SALES_DROP: "шт./день", det.SALES_GROWTH: "шт./день", det.PRICE_CHANGE: "₽", det.COMMISSION_CHANGE: "%",
    det.LOW_MARGIN: "%", det.LOW_STOCK: "дн.", det.PROMO_AUTO_ADDED: "%", det.PROMO_LOSS: "%", det.PAYOUT_MISSED: "₽",
    det.PAYOUT_DIFF: "₽", det.BANK_MISSING: "₽", det.BANK_DIFF: "₽", det.CASH_GAP: "₽", det.UNCATEGORIZED: "шт.",
    det.EXCESS_STOCK: "дн.", det.ACCRUAL_GAPS: "дн.",
}
SYNC_WINDOW = "/dashboard?sync=1"  # открывает окно «Синхронизация» (10.2)


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


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 товар, 2 товара, 5 товаров."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _span(r: list[str]) -> str:
    return _day(r[0]) if r[0] == r[1] else f"{_day(r[0])}–{_day(r[1])}"


def describe(
    type_: str, severity: str, before: Decimal | None, after: Decimal | None, threshold: Decimal | None,
    details: dict | None, product_id=None, product_name: str | None = None, today: date | None = None,
) -> Text:
    d = details or {}
    what = f"«{product_name}»" if product_name else "товара"
    product_link = f"/dashboard/products/{product_id}" if product_id else None

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

    if type_ == det.EXCESS_STOCK:
        qty, cost = d.get("excess_qty") or 0, d.get("cost")
        avg = d.get("avg_daily")
        selling = avg is not None and Decimal(avg) > 0
        title = f"Избыток: запаса на {num(after, 0)} дн." if selling and after is not None else f"Лежит без продаж: {d.get('available', 0)} шт."
        sales = f"продаётся {num(avg, 2)} шт. в день" if selling else "продаж за 30 дней не было"
        frozen = (f" Лишнее — около {qty} шт. на {rub(Decimal(cost) * qty)} по себестоимости: деньги заморожены, а Ozon "
                  "берёт за хранение." if cost is not None and qty else
                  f" Лишнее — около {qty} шт.; укажите себестоимость, чтобы видеть, сколько в нём денег." if qty else "")
        return Text(title, f"На складах {d.get('available', 0)} шт. {what}, {sales} — запаса больше порога «Избыток» "
                           f"({num(threshold, 0)} дн.).{frozen} Снизьте цену, добавьте товар в акцию или вывезите "
                           "лишнее — «На вывоз».", "/dashboard/warehouses/withdrawal")

    if type_ == det.SYNC_PROBLEM:
        titles = d.get("titles") or []
        problem = d.get("problem") or "данные не загружаются"
        hint = d.get("hint") or "Откройте окно «Синхронизация» и нажмите «Обновить»."
        short = problem.split(":")[0]  # «Ozon не принял ключ: он истёк…» — до двоеточия, подробности — в пояснении
        if d.get("all_ozon"):
            title = f"{short} — данные Ozon не обновляются"
        elif len(titles) > 1:
            title = f"{short} — не обновляются {', '.join(t.lower() for t in titles)}"
        else:
            lower = problem if problem.startswith("Ozon") else problem[:1].lower() + problem[1:]  # «Ozon» — с заглавной
            title = f"{titles[0] if titles else 'Данные'}: {lower}"
        last = d.get("last_success_at")
        tail = (f" Данные — на {_day(last, True)}: пока они не обновятся, цифры в разделах не меняются." if last
                else " Данные ещё не загружались — разделы с ними пустые.")
        why = f"{problem}. " if len(titles) > 1 and ":" in problem else ""
        return Text(title, why + hint + tail, d.get("href") or SYNC_WINDOW)

    if type_ == det.ACCRUAL_GAPS:
        n = d.get("count") or int(after or 0)
        spans = ", ".join(_span(r) for r in d.get("ranges") or [])
        more = f" и ещё {d['more_ranges']}" if d.get("more_ranges") else ""
        return Text(f"Нет начислений за {n} {plural(n, 'день', 'дня', 'дней')}",
                    f"Между загруженными днями нет данных Ozon: {spans}{more}. Выручка, прибыль и P&L за эти дни неполные "
                    "(это пропуск, а не ноль). Загрузите отчёт по начислениям за эти дни или обновите начисления в окне "
                    "«Синхронизация».", "/dashboard/analytics/reports")

    if type_ == det.REPORT_ERROR:
        error = f": {d['error'].rstrip('.')}" if d.get("error") else ""
        period = f" за {d['period']}" if d.get("period") else ""
        return Text(f"Отчёт не перечитался: {d.get('filename') or 'отчёт Ozon'}",
                    f"Отчёт{period} не удалось перечитать{error}. В P&L — итоги прошлого разбора. Перечитайте отчёт ещё "
                    "раз или загрузите файл заново — «Отчёты Ozon».", "/dashboard/analytics/reports")

    if type_ == det.MISSING_COST:
        n = d.get("count") or int(after or 0)
        top = ", ".join(f"{x.get('offer_id')} ({rub(x.get('revenue'))})" for x in d.get("top") or [])
        return Text(f"Продажи без себестоимости: {n} {plural(n, 'товар', 'товара', 'товаров')}",
                    f"За 30 дней продано на {rub(d.get('revenue'))} без себестоимости — прибыль по этим продажам не "
                    f"посчитана (не ноль). Больше всего: {top}. Укажите себестоимость — можно шаблоном Excel.",
                    "/dashboard/products/costs?missing=1")

    return Text(TYPE_TITLES.get(type_, type_), "", None)
