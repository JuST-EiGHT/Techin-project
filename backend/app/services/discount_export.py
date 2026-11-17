"""Заявки на скидку в Excel (шаг 4.3a) — те же строки, что на странице."""
from app.services.sync.finance import MSK
from app.services.xlsx_export import Col, table_xlsx

COLUMNS = [
    Col("Заявка Ozon", 14, "int"), Col("Артикул", 24), Col("Товар", 40), Col("SKU", 14, "int"), Col("Статус", 12),
    Col("Создана", 16), Col("Действует до", 16), Col("Ваша цена, ₽", 13, "money"),
    Col("Запрошенная цена, ₽", 14, "money"), Col("Скидка, %", 10, "number"), Col("Просят, шт.", 10, "int"),
    Col("Мин. для участия, ₽", 14, "money"), Col("Мин. цена в Ozon, ₽", 14, "money"),
    Col("Маржа при запрошенной, %", 14, "number"), Col("Прибыль с 1 шт., ₽", 14, "money"),
    Col("Прибыль за все, ₽", 14, "money"), Col("Остаток, шт.", 10, "int"), Col("Пометки", 30),
    Col("Одобрено по цене, ₽", 14, "money"), Col("Одобрено, шт.", 10, "int"),
]


def _dt(v) -> str | None:
    return None if v is None else v.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def to_xlsx(rows: list[dict]) -> bytes:
    data = []
    for r in rows:
        p = r["product"] or {}
        data.append([
            r["ozon_task_id"], p.get("offer_id"), p.get("name") or r["name"], r["sku"], r["state_title"],
            _dt(r["created_at"]), _dt(r["end_at"]), r["price"], r["requested_price"], r["discount_pct"],
            r["requested_quantity"], r["breakeven_price"], r["min_auto_price"], r["margin"], r["unit_profit"],
            r["total_profit"], r["stock"], ", ".join(r["mark_titles"]) or None, r["approved_price"],
            r["approved_quantity"],
        ])
    return table_xlsx("Заявки на скидку", COLUMNS, data, notes=[
        "Заявки покупателей на скидку из Ozon. Время — по Москве.",
        "Маржа и прибыль — при запрошенной цене, калькулятором цены сервиса (до налога на прибыль).",
        "Мин. для участия — цена, при которой прибыль с продажи 0: ниже — каждая продажа в минус.",
        "Пустая ячейка — нет данных (не 0): например, нет себестоимости.",
    ])
