"""Товары акции в Excel (правка владельца): участники и кандидаты с ценами — ваша, в акции (у кандидата — цена входа),
минимальная и целевая для участия, минимальная в Ozon, маржа и прибыль с 1 шт., другие акции товара. Для чтения и
работы, не шаблон загрузки (services/xlsx_export)."""
from datetime import date
from decimal import Decimal

from app.schemas.promotion import PromotionProductResponse, PromotionProductsResponse
from app.services.xlsx_export import Col, table_xlsx

COLUMNS = [
    Col("Статус", 12),
    Col("Артикул", 18),
    Col("SKU Ozon", 14),
    Col("Название", 40),
    Col("Ваша цена, ₽", 13, "money"),
    Col("Цена в акции / цена входа, ₽", 16, "money"),
    Col("Скидка, %", 10, "number"),
    Col("Мин. для участия, ₽", 14, "money"),
    Col("Целевая цена, ₽", 14, "money"),
    Col("Мин. цена в Ozon, ₽", 14, "money"),
    Col("Остаток, шт.", 11, "int"),
    Col("Маржа в акции, %", 12, "number"),
    Col("Прибыль с 1 шт., ₽", 14, "money"),
    Col("Если уйдёт весь остаток, ₽", 16, "money"),
    Col("Ниже минимальной", 12),
    Col("В других акциях", 50),
]


def _price(r: PromotionProductResponse) -> Decimal | None:
    if r.is_active:
        return r.action_price
    return r.max_action_price if r.max_action_price and r.max_action_price > 0 else r.action_price


def _others(r: PromotionProductResponse) -> str:
    parts = []
    for o in r.other_promotions:
        price = f"{o.action_price:.2f} ₽" if o.action_price is not None else "цена неизвестна"
        till = f", до {o.date_to:%d.%m.%Y}" if o.date_to else ""
        parts.append(f"{o.title} — {price}{' (дешевле)' if o.cheaper else ''}{till}{', заморожена' if o.is_frozen else ''}")
    return "; ".join(parts)


def promotion_xlsx(data: PromotionProductsResponse, today: date) -> bytes:
    p = data.promotion
    rows = [
        [
            ("участвует, авто" if r.added_automatically else "участвует") if r.is_active else "кандидат",
            r.product.offer_id, r.product.ozon_sku, r.product.name, r.price, _price(r), r.discount_pct,
            r.breakeven_price, r.target_price, r.min_price, r.stock_on_hand, r.margin_in_promo, r.unit_profit,
            r.whole_stock_profit, "да" if r.below_breakeven else "", _others(r),
        ]
        for r in data.items
    ]
    period = " — ".join(f"{d:%d.%m.%Y}" for d in (p.date_from, p.date_to) if d)
    notes = [
        f"Акция «{p.title}»",
        f"Период: {period or 'не указан'}. Выгрузка {today:%d.%m.%Y}.",
        "Ваша цена — цена товара в Ozon без акции.",
        "Цена в акции — по ней товар продаётся во время акции; от неё считаются комиссия, логистика, маржа и прибыль. "
        "У кандидата — цена входа: максимальная цена, по которой Ozon примет товар в акцию.",
        "Мин. для участия — цена без прибыли и без убытка (себестоимость, комиссия, логистика, реклама, доп. расходы, "
        "налоги — калькулятор цены). Ниже неё каждая продажа в минус. Целевая — цена с вашей целевой маржой.",
        "Мин. цена в Ozon — минимальная цена, заданная в Ozon: ниже неё Ozon цену в акциях не опускает.",
        "Маржа и прибыль с 1 шт. — при цене в акции, до налога на прибыль. Пусто — нет себестоимости или данных о "
        "комиссии и логистике.",
        "В других акциях — товар уже участвует в других идущих акциях; «дешевле» — там цена ниже, чем здесь.",
    ]
    if data.margin_note:
        notes.append(data.margin_note)
    return table_xlsx("Товары акции", COLUMNS, rows, notes=notes)
