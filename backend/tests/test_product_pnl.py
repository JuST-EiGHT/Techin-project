"""Прибыль по товарам (шаг 3.4): группы начислений, отнесение по отправлению, «Не распределено», API и CSV."""
import csv
import io
from datetime import date
from decimal import Decimal

from app.engine.product_pnl import LineIn, compute, group_of
from tests.test_pnl_api import add_api_day, pnl, user_id
from tests.test_report_pnl import MONTH, OWNER, WEEK, auth, import_reference_costs, set_profile, upload

D = date(2026, 9, 8)
WEEK_PARAMS = {"date_from": "2026-09-07", "date_to": "2026-09-13"}


def ln(type_name, amount, offer=None, qty=None, posting=None, day=D):
    return LineIn(day, type_name, Decimal(amount), qty, offer, "", "", posting)


def test_groups_by_type_name():
    assert group_of("Выручка") == "revenue"
    assert group_of("Вознаграждение за продажу") == group_of("Возврат вознаграждения") == "commission"
    for name in ("Логистика", "Обратная логистика", "Доставка до места выдачи", "Упаковка товара партнёрами",
                 "Обработка возвратов, отмен и невыкупов партнёрами", "Обеспечение материалами для упаковки товара"):
        assert group_of(name) == "logistics", name
    for name in ("Баллы за скидки", "Эквайринг", "Возврат выручки", "Звёздные товары", "Размещение на складе"):
        assert group_of(name) == "other", name


def test_attribution_cost_and_unallocated():
    lines = [
        ln("Выручка", "1000", "A", 2, "P1"), ln("Вознаграждение за продажу", "-200", "A", 2, "P1"),
        ln("Логистика", "-100", None, None, "P1"),  # без артикула, но отправление с одним товаром → A
        ln("Выручка", "500", "B", 1, "P2"), ln("Выручка", "300", "C", 1, "P2"),
        ln("Эквайринг", "-8", None, None, "P2"),  # в отправлении два товара → не делим
        ln("Размещение на складе", "-50"),  # без товара и отправления
        ln("Выручка", "700", "X", 1),  # нет себестоимости
    ]
    costs = {"A": (Decimal(100), None), "B": (Decimal(200), Decimal(1)), "C": (Decimal(100), None)}
    r = compute(lines, {"P1": {"A"}, "P2": {"B", "C"}}, lambda o, d: costs.get(o), True, Decimal("1.22"))
    rows = {row.offer_id: row for row in r.rows}
    a = rows["A"]
    assert (a.qty, a.revenue, a.commission, a.logistics, a.total, a.by_posting) == (
        2, Decimal(1000), Decimal(-200), Decimal(-100), Decimal(700), Decimal(-100)
    )
    assert a.cost == Decimal("244.00") and a.profit == Decimal("456.00") and a.margin == Decimal("45.60")
    assert a.avg_price == Decimal("500.00")
    assert rows["B"].cost == Decimal(200)  # у артикула своя ставка: без НДС
    x = rows["X"]
    assert x.cost_missing and (x.cost, x.profit, x.margin) == (None, None, None)  # не 0 (правило 5)
    assert r.unallocated.total == Decimal(-58) and r.unallocated.multi_product == Decimal(-8)
    assert r.period_total == sum((row.total for row in r.rows), Decimal(0)) + r.unallocated.total
    # плательщик НДС с вычетом — себестоимость без НДС
    r = compute(lines, {}, lambda o, d: costs.get(o), False, Decimal("1.22"))
    assert {row.offer_id: row.cost for row in r.rows}["A"] == Decimal(200)


async def by_product(client, tokens, **params):
    r = await client.get("/api/v1/pnl/by-product", params={**WEEK_PARAMS, **params}, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_reference_week_by_product(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    body = await by_product(client, tokens, limit=500)
    t, un = body["totals"], body["unallocated"]
    assert body["data_source"] == "xlsx" and body["cost_basis"] == "with_vat"
    # всё начисленное — по товарам или в «Не распределено»: сходится с P&L периода (BUSINESS_LOGIC §3)
    assert body["period_total"] == "10955.83"
    assert Decimal(t["total"]) + Decimal(un["total"]) == Decimal("10955.83")
    assert (t["revenue"], t["cost"], t["qty"], t["missing_cost"]) == ("23712.03", "12374.90", 39, 0)
    assert Decimal(t["profit"]) + Decimal(un["total"]) == Decimal("-1419.07")
    assert sum(1 for i in body["items"] if i["qty"]) == 16  # позиций с выручкой
    assert un["by_type"] == {
        "Страхование товара от массовых повреждений": "-2108.90",
        "Продвижение с оплатой за заказ": "-1728.51",  # номер кампании, а не отправления — товар неизвестен
        "Размещение на складе": "-1573.77",
    }
    top = body["items"][0]
    assert (top["offer_id"], top["qty"], top["revenue"], top["avg_price"]) == ("6004-2RS.CM.SRL.NAC.TH", 19, "10487.05",
                                                                               "551.95")
    assert (top["commission"], top["logistics"], top["cost"], top["profit"], top["margin"]) == (
        "-9162.18", "-1657.69", "5215.50", "3166.22", "30.19"
    )
    assert top["by_type"]["Выручка"] == "10487.05"
    returned = next(i for i in body["items"] if i["offer_id"] == "ACB355222.NAC.JP")
    assert (returned["qty"], returned["returned_qty"]) == (1, 1)
    # тот же отчёт по report_id — те же цифры
    report_id = (await client.get("/api/v1/reports", headers=auth(tokens))).json()["items"][0]["id"]
    same = await by_product(client, tokens, report_id=report_id, limit=500, date_from="2026-01-01", date_to="2026-01-31")
    assert same["totals"] == t and same["report_id"] == report_id


async def test_sort_search_and_pages(client, tokens):
    await import_reference_costs(client, tokens)
    await upload(client, tokens, WEEK)
    by_margin = (await by_product(client, tokens, sort="margin", order="desc", limit=500))["items"]
    margins = [Decimal(i["margin"]) for i in by_margin if i["margin"] is not None]
    assert margins == sorted(margins, reverse=True) and by_margin[-1]["margin"] is None  # пустые — в конце
    by_qty = (await by_product(client, tokens, sort="qty", order="asc"))["items"]
    assert [i["qty"] for i in by_qty] == sorted(i["qty"] for i in by_qty)
    found = await by_product(client, tokens, search="acb")
    assert found["total"] == 2 and {i["offer_id"] for i in found["items"]} == {"ACB355222.NAC.JP", "ACB325520.NAC.JP"}
    assert found["totals"]["products"] == 22  # итоги — по всем товарам периода
    page = await by_product(client, tokens, limit=5, offset=20)
    assert (page["total"], len(page["items"]), page["offset"]) == (22, 2, 20)
    r = await client.get("/api/v1/pnl/by-product", params={"sort": "price"}, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Сортировка: недопустимое значение")


async def test_vat_deduction_cost_without_vat_and_missing_cost(client, tokens):
    await set_profile(client, tokens, {"legal_form": "ooo", "regime": "osn", "vat_mode": "general",
                                       "vat_rate_pct": "22", "effective_from": "2026-01-01"})
    await client.put("/api/v1/costs/6004-2RS.CM.SRL.NAC.TH", json={"cost_price": "274.50"}, headers=auth(tokens))
    await upload(client, tokens, WEEK)
    body = await by_product(client, tokens, limit=500)
    assert body["cost_basis"] == "without_vat"  # НДС поставщика — к вычету
    items = {i["offer_id"]: i for i in body["items"]}
    assert items["6004-2RS.CM.SRL.NAC.TH"]["cost"] == "5215.50"  # 274,50 × 19 без НДС
    other = items["6306-2Z.C3.SRL.NAC.JP"]
    assert other["cost_missing"] and other["profit"] is None and other["margin"] is None
    assert body["totals"]["missing_cost"] == 15
    assert any(w.startswith("Не найдена себестоимость у 15 товаров") for w in body["warnings"])


async def test_api_lines_by_product(client, tokens, sessions):
    await client.put("/api/v1/costs/A-1", json={"cost_price": "100", "vat_multiplier": "1"}, headers=auth(tokens))
    uid = await user_id(client, tokens)
    await add_api_day(sessions, uid, date(2026, 9, 14), [
        ("Выручка", "1000", 2, "A-1"), ("Вознаграждение за продажу", "-150", None, "A-1"),
        ("Логистика", "-60", None, "A-1"), ("Подписка Premium", "-50", None, None),
    ])
    body = await by_product(client, tokens, date_from="2026-09-14", date_to="2026-09-14")
    item = body["items"][0]
    assert body["data_source"] == "api" and any(w.startswith("По данным Ozon API") for w in body["warnings"])
    assert (item["offer_id"], item["qty"], item["commission"], item["logistics"], item["cost"], item["profit"]) == (
        "A-1", 2, "-150.00", "-60.00", "200.00", "590.00"
    )
    assert body["unallocated"]["by_type"] == {"Подписка Premium": "-50.00"}


async def test_realization_report_and_isolation(client, tokens):
    month = await upload(client, tokens, MONTH)
    r = await client.get("/api/v1/pnl/by-product", params={"report_id": month["id"]}, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"].startswith("В отчёте о реализации нет удержаний Ozon по товарам")
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    login = {"email": "other@example.com", "password": "strongpass1"}
    other = (await client.post("/api/v1/auth/login", json=login)).json()
    r = await client.get("/api/v1/pnl/by-product", params={"report_id": month["id"]}, headers=auth(other))
    assert r.status_code == 404
    empty = await by_product(client, other)
    assert empty["items"] == [] and empty["data_source"] == "none" and empty["period_total"] == "0.00"


async def test_csv_export(client, tokens):
    await import_reference_costs(client, tokens)
    await client.patch("/api/v1/settings", json={"supplier_payment": "after_sale"}, headers=auth(tokens))
    await upload(client, tokens, WEEK)
    r = await client.get("/api/v1/pnl/by-product/export", params={**WEEK_PARAMS, "sort": "profit"},
                         headers=auth(tokens))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "filename*=UTF-8''%D0%9F%D1%80%D0%B8%D0%B1%D1%8B%D0%BB%D1%8C" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # BOM — Excel откроет кириллицу
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "Артикул" and rows[0][10] == "Долг поставщику, руб."  # подпись — по настройке
    assert rows[1][0] == "6004-2RS.CM.SRL.NAC.TH" and rows[1][11] == "3166,22" and rows[1][12] == "30,19"
    assert rows[-2][1].startswith("Не распределено") and rows[-2][9] == "-5411,18"
    assert rows[-1][1] == "Итого за период" and rows[-1][9] == "10955,83" and len(rows) == 22 + 3


async def test_cache_reset_after_cost_change(client, tokens):
    await upload(client, tokens, WEEK)
    before = await by_product(client, tokens, search="6004-2RS.CM")
    assert before["items"][0]["cost_missing"]
    await client.put("/api/v1/costs/6004-2RS.CM.SRL.NAC.TH", json={"cost_price": "225"}, headers=auth(tokens))
    after = await by_product(client, tokens, search="6004-2RS.CM")
    assert after["items"][0]["cost"] == "5215.50"  # 225 × 19 × 1,22
    assert (await pnl(client, tokens))["pnl"]["owed"] == "5215.50"  # и P&L периода тот же
