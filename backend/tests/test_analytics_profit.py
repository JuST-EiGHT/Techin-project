"""Шаг 10.9: «Аналитика прибыльности» — структура расходов (сумма групп = расходы P&L), разбор группы по товарам,
исходные строки начислений товара (итог = «начислено Ozon» товара), быстрые фильтры таблицы «Прибыли по товарам»."""
import uuid
from datetime import date
from decimal import Decimal

from app.engine import profit_structure as ps
from tests.test_pnl_api import add_api_day, pnl, user_id
from tests.test_product_pnl import WEEK_PARAMS, by_product
from tests.test_report_pnl import OWNER, WEEK, auth, import_reference_costs, set_profile, upload

D = Decimal


def test_groups_and_structure_sum():
    assert ps.group_of("Выручка") == ps.REVENUE and ps.group_of("Возврат выручки") == ps.RETURNS
    # комиссии и логистика — ровно колонки «Прибыли по товарам»; её «Прочее» — подробнее
    assert ps.group_of("Вознаграждение за продажу") == ps.group_of("Возврат вознаграждения") == ps.COMMISSION
    assert ps.group_of("Логистика") == ps.group_of("Обратная логистика") == ps.LOGISTICS
    assert ps.group_of("Продвижение с оплатой за заказ") == ps.group_of("Оплата за клик") == ps.ADS
    assert ps.group_of("Размещение на складе") == ps.group_of("Услуга хранения") == ps.STORAGE
    for name in ("Страхование товара от массовых повреждений", "Эквайринг", "Баллы за скидки", "Подписка Premium"):
        assert ps.group_of(name) == ps.OTHER, name
    by_type = {"Выручка": D(1000), "Вознаграждение за продажу": D(-150), "Возврат вознаграждения": D(10),
               "Логистика": D(-60), "Компенсация": D(20)}
    s = ps.structure(by_type, D(300), adjustment=D(-5))
    assert [g.key for g in s.groups] == [ps.COST, ps.COMMISSION, ps.LOGISTICS, ps.OTHER, ps.ADJUSTMENT]
    groups = {g.key: g for g in s.groups}
    assert groups[ps.COMMISSION].amount == D(140) and list(groups[ps.COMMISSION].types) == [
        "Вознаграждение за продажу", "Возврат вознаграждения"]
    assert groups[ps.OTHER].amount == D(-20)  # доплата Ozon уменьшает расходы
    assert groups[ps.ADJUSTMENT].amount == D(5)  # пришло на 5 ₽ меньше, чем по отчётам
    # прибыль до налога = выручка + начисления − себестоимость + поправка
    assert s.expenses == D(485) and s.profit == D(1000) - D(150) + D(10) - D(60) + D(20) - D(300) - D(5)
    assert groups[ps.COST].share(s.revenue) == D("30.0")
    assert ps.structure({"Выручка": D(500)}, None).groups == []  # себестоимость не посчитать — группы нет, а не 0


async def get(client, tokens, path, **params):
    r = await client.get(f"/api/v1/analytics/profit/{path}", params={**WEEK_PARAMS, **params}, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_reference_week_structure_equals_pnl(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    report = await upload(client, tokens, WEEK)
    p = await pnl(client, tokens)
    s = await get(client, tokens, "structure")
    assert s["scope"] == "store" and s["has_data"] and s["missing_cost"] == 0
    # сумма групп = расходы = выручка − прибыль до налога P&L, до копейки
    assert s["revenue"] == p["by_type"]["Выручка"]
    assert s["profit"] == p["pnl"]["profit_before_tax"] == "-1419.07" and s["net"] == p["pnl"]["net"]
    assert sum(D(g["amount"]) for g in s["groups"]) == D(s["expenses"]) == D(s["revenue"]) - D(s["profit"])
    groups = {g["key"]: g for g in s["groups"]}
    assert groups["cost"]["amount"] == p["pnl"]["owed"] == "12374.90" and groups["cost"]["types"] == []
    assert {t["name"] for t in groups["ads"]["types"]} >= {"Продвижение с оплатой за заказ"}
    assert {t["name"] for t in groups["storage"]["types"]} == {"Размещение на складе"}
    # каждая статья P&L — ровно в одной группе
    names = [t["name"] for g in s["groups"] for t in g["types"]]
    assert sorted(names) == sorted(n for n in p["by_type"] if n != "Выручка")
    assert s["previous"] is None  # за прошлую неделю данных нет

    # ручная сумма «сколько пришло» — отдельная группа, сумма всё так же равна расходам P&L
    await client.patch(f"/api/v1/reports/{report['id']}", json={"manual_total": "10000"}, headers=auth(tokens))
    p2 = await pnl(client, tokens)
    s2 = await get(client, tokens, "structure")
    adj = {g["key"]: g for g in s2["groups"]}["adjustment"]
    assert adj["amount"] == "955.83" and s2["profit"] == p2["pnl"]["profit_before_tax"]
    assert sum(D(g["amount"]) for g in s2["groups"]) == D(s2["revenue"]) - D(s2["profit"])


async def test_reference_week_group_detail_and_lines(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    report = await upload(client, tokens, WEEK)
    items = {i["offer_id"]: i for i in (await by_product(client, tokens, limit=500))["items"]}
    s = {g["key"]: g for g in (await get(client, tokens, "structure"))["groups"]}

    # статья → товары: по товарам и «Не распределено» — вся сумма группы
    for key in ("commission", "logistics", "ads", "storage", "other"):
        if key not in s:
            continue
        g = await get(client, tokens, "group", key=key)
        total = sum(D(x["amount"]) for x in g["products"]) + D(g["unallocated"] or 0)
        assert total == D(s[key]["amount"]), key
    storage = await get(client, tokens, "group", key="storage")
    assert storage["products"] == [] and storage["unallocated"] == "1573.77"  # размещение — без товара
    cost = await get(client, tokens, "group", key="cost")
    # по товарам — как в таблице «Прибыли по товарам» (себестоимость товара округлена до копейки)
    assert sum(D(x["amount"]) for x in cost["products"]) == sum(D(i["cost"]) for i in items.values() if i["cost"])
    assert cost["unallocated"] is None
    top = cost["products"][0]
    assert D(top["amount"]) == max(D(i["cost"]) for i in items.values() if i["cost"])
    assert top["id"] is None  # товары только из отчёта — в каталоге их нет, ведём по артикулу

    # товар → исходные строки начислений: итог = «начислено Ozon» товара, у строки — номер, дата, отчёт
    offer = max(items.values(), key=lambda i: D(i["revenue"]))["offer_id"]
    lines = await get(client, tokens, "lines", offer_id=offer)
    assert lines["total"] == items[offer]["total"] and lines["count"] == len(lines["lines"]) > 0
    first = lines["lines"][0]
    assert first["source"] == "xlsx" and first["report"]["id"] == report["id"] and first["posting_number"]
    assert {ln["group"] for ln in lines["lines"]} >= {"revenue", "commission"}
    assert sum(D(ln["amount"]) for ln in lines["lines"] if ln["group"] == "revenue") == D(items[offer]["revenue"])

    r = await client.get("/api/v1/analytics/profit/group", params={**WEEK_PARAMS, "key": "x"}, headers=auth(tokens))
    assert r.status_code == 400


async def test_api_products_filter_lines_and_quick_filters(client, tokens, sessions):
    await set_profile(client, tokens, OWNER)
    for offer, cost in (("A-1", "100"), ("B-2", "400")):
        await client.put(f"/api/v1/costs/{offer}", json={"cost_price": cost, "vat_multiplier": "1"}, headers=auth(tokens))
    uid = await user_id(client, tokens)
    day = date(2026, 9, 8)
    await add_api_day(sessions, uid, day, [
        ("Выручка", "1000", 2, "A-1"), ("Вознаграждение за продажу", "-150", None, "A-1"), ("Логистика", "-60", None, "A-1"),
        ("Выручка", "500", 1, "B-2"), ("Вознаграждение за продажу", "-75", None, "B-2"), ("Логистика", "-40", None, "B-2"),
        ("Выручка", "300", 1, "C-3"), ("Подписка Premium", "-50", None, None),
    ])
    s = await get(client, tokens, "structure")
    assert (s["revenue"], s["profit"], s["missing_cost"]) == ("1800.00", "825.00", 1)  # C-3 без себестоимости
    assert sum(D(g["amount"]) for g in s["groups"]) == D(s["expenses"])

    a = (await client.get("/api/v1/analytics/products", params={"search": "A-1"}, headers=auth(tokens))).json()[0]
    one = await get(client, tokens, "structure", product_id=a["id"])
    groups = {g["key"]: g["amount"] for g in one["groups"]}
    assert one["scope"] == "products" and (one["revenue"], one["profit"]) == ("1000.00", "590.00")
    assert groups == {"cost": "200.00", "commission": "150.00", "logistics": "60.00"}  # без «Подписки» — она не товара
    detail = await get(client, tokens, "group", key="logistics", product_id=a["id"])
    assert [(p["offer_id"], p["amount"], p["id"]) for p in detail["products"]] == [("A-1", "60.00", a["id"])]
    assert detail["unallocated"] is None
    every = await get(client, tokens, "group", key="other")
    assert every["products"] == [] and every["unallocated"] == "50.00"

    lines = await get(client, tokens, "lines", offer_id="A-1")
    assert [(ln["type_name"], ln["amount"], ln["source"], ln["report"]) for ln in lines["lines"]] == [
        ("Выручка", "1000.00", "api", None), ("Вознаграждение за продажу", "-150.00", "api", None),
        ("Логистика", "-60.00", "api", None)]
    assert lines["total"] == "790.00" and lines["lines"][0]["day"] == "2026-09-08"

    # быстрые фильтры: убыток, низкая маржа (порог — настройка), нет себестоимости; числа у кнопок
    body = await by_product(client, tokens)
    assert body["filter_counts"] == {"all": 3, "loss": 1, "low_margin": 1, "no_cost": 1} and D(body["low_margin"]) == 10
    assert [i["offer_id"] for i in (await by_product(client, tokens, filter="loss"))["items"]] == ["B-2"]  # −15 ₽
    assert [i["offer_id"] for i in (await by_product(client, tokens, filter="no_cost"))["items"]] == ["C-3"]
    await client.patch("/api/v1/settings", json={"low_margin_pct": "60"}, headers=auth(tokens))
    low = await by_product(client, tokens, filter="low_margin")
    assert {i["offer_id"] for i in low["items"]} == {"A-1", "B-2"} and D(low["low_margin"]) == 60  # 59 % и −3 %
    assert (await by_product(client, tokens, filter=["low_margin", "loss"]))["total"] == 1
    csv_text = (await client.get("/api/v1/pnl/by-product/export", params={**WEEK_PARAMS, "filter": "loss"},
                                 headers=auth(tokens))).content.decode("utf-8-sig")
    assert [line.split(";")[0] for line in csv_text.splitlines()[1:-2]] == ["B-2"]  # CSV — по тем же фильтрам
    only_a = await by_product(client, tokens, product_id=a["id"])
    assert [i["offer_id"] for i in only_a["items"]] == ["A-1"] and only_a["filter_counts"]["all"] == 1
    assert (await client.get("/api/v1/pnl/by-product", params={**WEEK_PARAMS, "product_id": str(uuid.uuid4())},
                             headers=auth(tokens))).status_code == 404


async def test_no_data(client, tokens):
    s = await get(client, tokens, "structure")
    assert s["has_data"] is False and s["groups"] == [] and s["revenue"] is None
    lines = await get(client, tokens, "lines", offer_id="A-1")
    assert lines["lines"] == [] and lines["total"] == "0.00"
