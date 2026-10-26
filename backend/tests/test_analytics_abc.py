"""Шаг 10.10: ABC-анализ — группы по накопленной доле (ровно на границе, переход через границу, ничья, ноль и убыток,
один товар, неизвестное значение), сводка, матрица «выручка × прибыль»; API — критерии, прошлый период и смена группы,
границы из настроек, фильтр товаров, Excel."""
import io
import uuid
from datetime import date
from decimal import Decimal

from openpyxl import load_workbook

from app.engine import abc
from tests.test_pnl_api import add_api_day, user_id
from tests.test_report_pnl import OWNER, auth, set_profile

D = Decimal


def groups(values: dict, bounds=abc.Bounds()) -> dict:
    return {r.key: r.group for r in abc.classify({k: None if v is None else D(v) for k, v in values.items()}, bounds)}


def test_exactly_on_boundary_and_crossing():
    # 50 + 30 = ровно 80 % — оба A; до третьего уже 80 % — B; до четвёртого 95 % — C
    assert groups({"a": 50, "b": 30, "c": 15, "d": 5}) == {"a": "A", "b": "A", "c": "B", "d": "C"}
    # второй переходит границу 80 % (70 → 90) — ещё A: без него 80 % не набрать
    assert groups({"a": 70, "b": 20, "c": 6, "d": 4}) == {"a": "A", "b": "A", "c": "B", "d": "C"}
    rows = {r.key: r for r in abc.classify({"a": D(50), "b": D(30), "c": D(15), "d": D(5)})}
    assert (rows["b"].rank, rows["b"].share, rows["b"].cum_share) == (2, D(30), D(80))
    assert rows["d"].cum_share == D(100)


def test_ties_share_group():
    # 40 | 20, 20, 20 — до первого из равных 40 %, все трое — A, хотя последний уже за 80 %
    assert groups({"x": 40, "b": 20, "a": 20, "c": 20}) == {"x": "A", "a": "A", "b": "A", "c": "A"}
    # 60 | 10, 10, 10, 10: до равных 60 % — все четыре A; порядок равных — по ключу
    rows = abc.classify({"m": D(60), "d": D(10), "b": D(10), "c": D(10), "a": D(10)})
    assert [r.key for r in rows] == ["m", "a", "b", "c", "d"] and {r.group for r in rows} == {"A"}
    # c и d равны: до c — 90 %, оба B, хотя до d уже 95 %
    assert groups({"a": 80, "b": 10, "c": 5, "d": 5}) == {"a": "A", "b": "B", "c": "B", "d": "B"}


def test_zero_negative_unknown_and_one_product():
    rows = {r.key: r for r in abc.classify({"a": D(100), "z": D(0), "l": D(-30), "n": None})}
    assert (rows["a"].group, rows["a"].share) == ("A", D(100))
    assert (rows["z"].group, rows["z"].loss, rows["z"].share) == ("C", False, D(0))
    assert (rows["l"].group, rows["l"].loss, rows["l"].share, rows["l"].rank) == ("C", True, None, None)
    assert (rows["n"].group, rows["n"].value) == (None, None)  # прибыль без себестоимости — не 0
    assert groups({"only": 5}) == {"only": "A"}
    assert groups({"only": -5}) == {"only": "C"} and groups({}) == {}


def test_summary_matrix_bounds_and_change():
    rows = abc.classify({"a": D(50), "b": D(30), "c": D(15), "d": D(5), "l": D(-10)})
    s = {g.group: g for g in abc.summary(rows)}
    assert (s["A"].count, s["A"].value, s["A"].share, s["A"].product_share) == (2, D(80), D(80), D(40))
    assert (s["C"].count, s["C"].value, s["C"].share) == (2, D(-5), D(5))  # с убыточным
    assert groups({"a": 50, "b": 30, "c": 15, "d": 5}, abc.Bounds(D(50), D(90))) == {"a": "A", "b": "B", "c": "B", "d": "C"}
    m = abc.matrix({"a": "A", "b": "A", "c": "C", "x": None}, {"a": "A", "b": "C", "c": None})
    assert m == {("A", "A"): ["a"], ("A", "C"): ["b"], ("C", None): ["c"]}
    assert abc.change_pct(D(150), D(100)) == D(50) and abc.change_pct(D(-50), D(-100)) == D(50)
    assert abc.change_pct(D(10), D(0)) is None and abc.change_pct(D(10), None) is None


# ── API ──
SEP = date(2026, 9, 8)
AUG = date(2026, 8, 8)  # прошлый сопоставимый период для 01–30.09 — август


async def seed(client, tokens, sessions):
    await set_profile(client, tokens, OWNER)
    for offer, cost in (("A-1", "100"), ("B-2", "300"), ("C-3", "10"), ("D-4", "50")):
        await client.put(f"/api/v1/costs/{offer}", json={"cost_price": cost, "vat_multiplier": "1"}, headers=auth(tokens))
    uid = await user_id(client, tokens)
    # сентябрь: выручка A 5000 (10 шт.), B 3000 (5), C 1500 (30), D 500 (1), E 400 (без себестоимости)
    await add_api_day(sessions, uid, SEP, [
        ("Выручка", "5000", 10, "A-1"), ("Выручка", "3000", 5, "B-2"), ("Логистика", "-1700", None, "B-2"),
        ("Выручка", "1500", 30, "C-3"), ("Выручка", "500", 1, "D-4"), ("Выручка", "400", 2, "E-5"),
    ])
    # август: B продавался больше всех, A меньше
    await add_api_day(sessions, uid, AUG, [("Выручка", "1000", 2, "A-1"), ("Выручка", "6000", 10, "B-2")])
    return uid


async def get(client, tokens, path="abc", **params):
    r = await client.get(f"/api/v1/analytics/{path}", params={"date_from": "2026-09-01", "date_to": "2026-09-30", **params},
                         headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r


async def test_api_abc_revenue_profit_qty(client, tokens, sessions):
    await seed(client, tokens, sessions)
    body = (await get(client, tokens)).json()
    items = {i["product"]["offer_id"]: i for i in body["items"]}
    # выручка 10 400: A 48 % → A; B до него 48 % → A (переходит 80 %); C до него 76,9 % → A; D 91,3 % → B; E 96,2 % → C
    assert [i["product"]["offer_id"] for i in body["items"]] == ["A-1", "B-2", "C-3", "D-4", "E-5"]
    assert {k: i["group"] for k, i in items.items()} == {"A-1": "A", "B-2": "A", "C-3": "A", "D-4": "B", "E-5": "C"}
    assert (body["total"], body["bounds"], body["previous"]["date_from"]) == ("10400.00", {"a": 80, "b": 95}, "2026-08-01")
    assert (items["A-1"]["share"], items["C-3"]["cum_share"]) == ("48.08", "91.35")
    # прошлый период и смена группы: B был A и остался; A — B → A (1000 из 7000); C — новый
    assert (items["A-1"]["prev_group"], items["A-1"]["prev_value"], items["A-1"]["change_pct"]) == ("B", "1000.00", "400.00")
    assert items["C-3"]["prev_group"] is None and items["C-3"]["change_pct"] is None
    g = {x["group"]: x for x in body["groups"]}
    assert (g["A"]["count"], g["A"]["value"], g["B"]["count"], g["C"]["count"]) == (3, "9500.00", 1, 1)

    # прибыль: A 4000, B 3000 − 1700 − 1500 = −200 (убыток), C 1200, D 450, E — без себестоимости
    profit = (await get(client, tokens, by="profit")).json()
    p = {i["product"]["offer_id"]: i for i in profit["items"]}
    assert (p["B-2"]["group"], p["B-2"]["loss"], p["B-2"]["share"]) == ("C", True, None)
    assert (p["E-5"]["group"], p["E-5"]["value"]) == (None, None) and profit["unknown"] == 1
    assert profit["loss"] == {"count": 1, "value": "-200.00"} and profit["total"] == "5650.00"
    # из 5650: A 70,8 % → A; C до него 70,8 % → A; D до него 92 % → B
    assert {k: v["group"] for k, v in p.items() if v["group"]} == {"A-1": "A", "C-3": "A", "D-4": "B", "B-2": "C"}
    # матрица выручка × прибыль: B — A по выручке, C по прибыли (убыток); E — нет себестоимости
    cells = {(c["revenue_group"], c["profit_group"]): c["count"] for c in profit["matrix"]}
    assert cells == {("A", "A"): 2, ("A", "C"): 1, ("B", "B"): 1, ("C", None): 1}
    assert items["B-2"]["profit_group"] == "C" and items["B-2"]["profit_loss"] is True

    qty = (await get(client, tokens, by="qty")).json()
    assert [(i["product"]["offer_id"], i["group"]) for i in qty["items"][:2]] == [("C-3", "A"), ("A-1", "A")]
    assert qty["total"] == "48.00"


async def test_api_bounds_filter_export_and_errors(client, tokens, sessions):
    await seed(client, tokens, sessions)
    r = await client.patch("/api/v1/settings", json={"abc_a_pct": 50, "abc_b_pct": 90}, headers=auth(tokens))
    assert r.status_code == 200 and (r.json()["abc_a_pct"], r.json()["abc_b_pct"]) == (50, 90)
    body = (await get(client, tokens)).json()
    assert body["bounds"] == {"a": 50, "b": 90}
    assert {i["product"]["offer_id"]: i["group"] for i in body["items"]} == {
        "A-1": "A", "B-2": "A", "C-3": "B", "D-4": "C", "E-5": "C"}
    bad = await client.patch("/api/v1/settings", json={"abc_a_pct": 90}, headers=auth(tokens))
    assert bad.status_code == 400 and "меньше границы группы B" in bad.json()["detail"]

    a = (await client.get("/api/v1/analytics/products", params={"search": "A-1"}, headers=auth(tokens))).json()[0]
    b = (await client.get("/api/v1/analytics/products", params={"search": "D-4"}, headers=auth(tokens))).json()[0]
    two = (await get(client, tokens, product_id=[a["id"], b["id"]])).json()
    assert [(i["product"]["offer_id"], i["group"]) for i in two["items"]] == [("A-1", "A"), ("D-4", "C")]

    r = await get(client, tokens, path="abc/export", by="profit")
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(r.content)).active
    assert [ws.cell(1, j).value for j in (1, 3, 4)] == ["Артикул", "Группа", "Показатель"]
    assert [ws.cell(i, 1).value for i in range(2, 7)] == ["A-1", "C-3", "D-4", "B-2", "E-5"]
    assert ws.cell(5, 12).value == "да" and ws.cell(6, 3).value == "нет данных"

    assert (await client.get("/api/v1/analytics/abc", params={"date_from": "2026-09-01", "date_to": "2026-09-30",
                                                              "by": "x"}, headers=auth(tokens))).status_code == 400
    assert (await client.get("/api/v1/analytics/abc", params={"date_from": "2026-09-01", "date_to": "2026-09-30",
                                                              "product_id": str(uuid.uuid4())},
                             headers=auth(tokens))).status_code == 404


async def test_api_no_data(client, tokens):
    body = (await get(client, tokens)).json()
    assert body["items"] == [] and body["data_source"] == "none" and body["total"] == "0.00"
    assert [g["count"] for g in body["groups"]] == [0, 0, 0] and body["matrix"] == []
