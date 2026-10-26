"""Шаг 10.15: себестоимость — сопоставление артикулов файла с товарами Ozon (похожие артикулы, привязка запоминается,
перепривязка и отвязка — записи не удаляются, конфликт привязки — ошибка строки) и полнота (товары с продажами без
себестоимости, доля выручки, список «заполнить»; нет начислений — нет данных, а не 0)."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.engine.offer_match import normalize, suggest
from app.models.offer_alias import OfferAlias
from app.models.product import Product
from app.models.product_cost import ProductCost
from app.services.cost_import import COST_TITLE
from app.services.sync.finance import today_msk
from tests.test_costs import auth, import_file, xlsx
from tests.test_pnl_api import add_api_day, user_id

CATALOG = ("6001-2RS", "6002-2RS", "HK1010", "6205-ZZ")


def test_normalize_and_suggest():
    assert normalize(" 6001-2RS.C3 ") == normalize("6001 2rs c3") == "60012rsc3"
    assert normalize("НК1010") == normalize("hk-1010") == "hk1010"  # кириллица, похожая на латиницу
    assert suggest("6001 2rs", list(CATALOG))[0] == "6001-2RS"
    assert suggest("НК1010", list(CATALOG)) == ["HK1010"]
    assert suggest("6205", list(CATALOG)) == ["6205-ZZ"]  # артикул файла — часть артикула Ozon
    assert suggest("XYZ-999", list(CATALOG)) == []
    assert suggest("6001-2RS", list(CATALOG)) == ["6002-2RS"]  # сам артикул — не подсказка


async def seed_catalog(client, tokens, sessions) -> uuid.UUID:
    uid = await user_id(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        for n, offer in enumerate(CATALOG):
            db.add(Product(user_id=uid, ozon_product_id=100 + n, ozon_sku=500 + n, offer_id=offer, name=f"Товар {offer}",
                           synced_at=now))
        await db.commit()
    return uid


FILE = [["Артикул", COST_TITLE], ["6001 2rs", 150], ["НК1010", 200], ["XYZ-999", 10], ["6002-2RS", 120]]


async def preview(client, tokens, rows=FILE) -> dict:
    r = await import_file(client, tokens, "costs.xlsx", xlsx(rows), dry_run=True)
    assert r.status_code == 200, r.text
    return {row["line"]: row for row in r.json()["rows"]}


async def test_unknown_articles_suggest_bind_and_remember(client, tokens, sessions):
    await seed_catalog(client, tokens, sessions)
    rows = await preview(client, tokens)
    assert (rows[2]["unknown"], rows[2]["suggestions"][0], rows[2]["status"]) == (True, "6001-2RS", "new")
    assert rows[3]["suggestions"] == ["HK1010"] and rows[4]["unknown"] and rows[4]["suggestions"] == []
    assert rows[5]["unknown"] is False and rows[5]["suggestions"] == []

    # привязать — следующий предпросмотр сопоставит сам; в строке видно, как было в файле
    r = await client.post("/api/v1/costs/aliases", json={"alias": "6001 2rs", "offer_id": "6001-2RS"}, headers=auth(tokens))
    assert r.status_code == 201, r.text
    assert (r.json()["alias"], r.json()["offer_id"], r.json()["name"]) == ("6001 2rs", "6001-2RS", "Товар 6001-2RS")
    rows = await preview(client, tokens)
    assert (rows[2]["offer_id"], rows[2]["matched_from"], rows[2]["unknown"]) == ("6001-2RS", "6001 2rs", False)

    # применить: цена — у товара Ozon, артикул из файла не появился
    r = await import_file(client, tokens, "costs.xlsx", xlsx(FILE))
    assert r.status_code == 200 and r.json()["counts"]["new"] == 4
    async with sessions() as db:
        offers = set((await db.execute(select(ProductCost.offer_id))).scalars())
    assert "6001-2RS" in offers and "6001 2rs" not in offers

    # привязка к артикулу, который уже есть в файле, — ошибка строки, а не две цены одному товару
    r = await client.post("/api/v1/costs/aliases", json={"alias": "НК1010", "offer_id": "6002-2RS"}, headers=auth(tokens))
    assert r.status_code == 201
    rows = await preview(client, tokens)
    assert rows[3]["status"] == "error" and "уже есть в строке 5" in rows[3]["message"]

    # перепривязка — прежняя запись снята, не удалена; отвязка — так же
    r = await client.post("/api/v1/costs/aliases", json={"alias": "нк-1010", "offer_id": "HK1010"}, headers=auth(tokens))
    assert r.status_code == 201
    listed = (await client.get("/api/v1/costs/aliases", headers=auth(tokens))).json()
    assert sorted((a["alias"], a["offer_id"]) for a in listed) == [("6001 2rs", "6001-2RS"), ("нк-1010", "HK1010")]
    alias_id = next(a["id"] for a in listed if a["offer_id"] == "6001-2RS")
    assert (await client.delete(f"/api/v1/costs/aliases/{alias_id}", headers=auth(tokens))).status_code == 204
    assert (await client.delete(f"/api/v1/costs/aliases/{alias_id}", headers=auth(tokens))).status_code == 404
    async with sessions() as db:
        records = (await db.execute(select(OfferAlias))).scalars().all()
    assert len(records) == 3 and sum(r.removed_at is None for r in records) == 1

    # нельзя: товара нет в каталоге; артикул и так в каталоге
    bad = await client.post("/api/v1/costs/aliases", json={"alias": "ABC", "offer_id": "NOPE"}, headers=auth(tokens))
    assert bad.status_code == 400 and "нет в каталоге" in bad.json()["detail"]
    bad = await client.post("/api/v1/costs/aliases", json={"alias": "6205-ZZ", "offer_id": "6001-2RS"}, headers=auth(tokens))
    assert bad.status_code == 400 and "и так есть" in bad.json()["detail"]


async def test_aliases_belong_to_store(client, tokens, sessions):
    uid = await seed_catalog(client, tokens, sessions)
    r = await client.post("/api/v1/costs/aliases", json={"alias": "6001 2rs", "offer_id": "6001-2RS"}, headers=auth(tokens))
    alias_id = r.json()["id"]
    async with sessions() as db:
        from app.models.user import User

        store = User(owner_id=uid, store_name="Второй")
        db.add(store)
        await db.commit()
        other = {**auth(tokens), "X-Store-Id": str(store.id)}
    assert (await client.get("/api/v1/costs/aliases", headers=other)).json() == []
    assert (await client.delete(f"/api/v1/costs/aliases/{alias_id}", headers=other)).status_code == 404


async def test_coverage(client, tokens, sessions):
    uid = await seed_catalog(client, tokens, sessions)
    # нет начислений — продажи неизвестны: доля — нет данных, а не 0
    empty = (await client.get("/api/v1/costs/coverage", headers=auth(tokens))).json()
    assert empty["data_source"] == "none" and empty["revenue_share_without_cost"] is None and empty["items"] == []
    assert (empty["catalog_total"], empty["catalog_with_cost"]) == (4, 0)

    await client.put("/api/v1/costs/6001-2RS", json={"cost_price": "100", "vat_multiplier": "1"}, headers=auth(tokens))
    await add_api_day(sessions, uid, today_msk() - timedelta(days=2), [
        ("Выручка", "1000", 2, "6001-2RS"), ("Выручка", "600", 1, "HK1010"), ("Выручка", "400", 4, "6205-ZZ"),
    ])
    body = (await client.get("/api/v1/costs/coverage", headers=auth(tokens))).json()
    assert (body["catalog_total"], body["catalog_with_cost"], body["sold_total"], body["sold_without_cost"]) == (4, 1, 3, 2)
    # без себестоимости: 600 + 400 из 2000 — 50 %; больше выручки — первым
    assert (body["revenue_total"], body["revenue_without_cost"], body["revenue_share_without_cost"]) == (
        "2000.00", "1000.00", "50.0")
    assert [(i["offer_id"], i["share"]) for i in body["items"]] == [("HK1010", "30.0"), ("6205-ZZ", "20.0")]
    assert body["items"][0]["product_id"] is not None and body["items"][0]["name"]
    assert (await client.get("/api/v1/costs/coverage", params={"days": 3}, headers=auth(tokens))).status_code == 400
