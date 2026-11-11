"""Цены (шаг 4.2): входные данные калькулятора по товару, POST /price/calculate, таблица цен, подсказки, настройки."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.accrual import Accrual, AccrualDay
from app.models.product import Product, ProductPrice
from app.models.product_cost import BEGINNING, ProductCost
from app.models.promotion import Promotion, PromotionProduct
from app.services.pnl_cache import cache as pnl_cache
from app.services.sync.finance import today_msk

D = Decimal


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def user_id_of(client, tokens) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])


async def add_sales(db, user_id, product: Product, day: date, qty: int = 1, price: D = D("459"), logistics: D = D("60")):
    """Продажа из Ozon API за день: «Выручка» и «Логистика» по товару (день отмечен загруженным)."""
    if await db.get(AccrualDay, (user_id, day)) is None:
        db.add(AccrualDay(user_id=user_id, day=day, synced_at=datetime.now(timezone.utc), entries=2))
    scope = f"posting:{product.offer_id}:{day}"
    db.add(Accrual(user_id=user_id, product_id=product.id, type_name="Выручка", accrual_date=day, amount=price * qty,
                   quantity=qty, source="api", sync_scope=scope, line_no=1))
    db.add(Accrual(user_id=user_id, product_id=product.id, type_name="Логистика", accrual_date=day,
                   amount=-logistics * qty, quantity=qty, source="api", sync_scope=scope, line_no=2))


async def seed(sessions, user_id) -> dict[str, Product]:
    """A — продаётся (логистика 60 ₽), цена 600; B — дешёвый, в минус, в акции; C — без продаж за 90 дней (средняя по
    магазину); D — без себестоимости; E — продавался 60 дней назад (логистика 80 ₽)."""
    today = today_msk()
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        p = {}
        for n, (key, price) in enumerate([("A", "600"), ("B", "400"), ("C", "1000"), ("D", "700"), ("E", "520")], 1):
            p[key] = Product(user_id=user_id, ozon_product_id=n, ozon_sku=500 + n, offer_id=f"ART-{key}",
                             name=f"Подшипник {key}", synced_at=now)
            db.add(p[key])
            await db.flush()
            db.add(ProductPrice(product_id=p[key].id, snapshot_at=now, price=D(price), commission_pct=D("38"),
                                acquiring=D(price) * D("0.015")))  # Ozon отдаёт рубли: 1,5 % от цены
            if key != "D":
                db.add(ProductCost(user_id=user_id, offer_id=f"ART-{key}", cost_price=D("164"), vat_multiplier=D("1.22"),
                                   effective_from=BEGINNING, source="manual", created_at=now))
        for i in range(1, 5):  # A: 4 продажи за 30 дней, логистика 60 ₽ на штуку
            await add_sales(db, user_id, p["A"], today - timedelta(days=i))
        await add_sales(db, user_id, p["B"], today - timedelta(days=5), qty=2, price=D("400"), logistics=D("30"))
        await add_sales(db, user_id, p["E"], today - timedelta(days=60), logistics=D("80"))
        promo = Promotion(user_id=user_id, ozon_action_id=1, title="Распродажа", date_to=now + timedelta(days=3),
                          is_participating=True, synced_at=now)
        db.add(promo)
        await db.flush()
        db.add(PromotionProduct(promotion_id=promo.id, product_id=p["A"].id, action_price=D("500"), is_active=True,
                                first_seen_at=now, synced_at=now))
        await db.commit()
        return p


async def calc(client, tokens, product_id, **overrides):
    r = await client.post("/api/v1/price/calculate", json={"product_id": str(product_id), **overrides}, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_calculate_with_ozon_data(client, tokens, sessions):
    p = await seed(sessions, await user_id_of(client, tokens))
    d = await calc(client, tokens, p["A"].id)
    i = d["inputs"]
    # реклама — по начислениям (её нет — 0), маржа — 20 % по умолчанию (не цифры владельца), налог не задан
    assert D(i["cost_with_vat"]) == D("200.08") and D(i["commission_pct"]) == 38 and D(i["ad_pct"]) == 0
    assert D(i["target_margin_pct"]) == 20 and D(i["price"]) == 600 and D(i["promo_price"]) == 500
    assert (D(i["logistics"]), i["logistics_source"]) == (D("60"), "product")
    assert "4 шт." in i["logistics_text"] and "30 дн." in i["logistics_text"]
    assert i["sources"]["commission_pct"] == "из Ozon (FBO)" and i["sources"]["ad_pct"] == "по начислениям за 30 дн."
    # 4.2a: возвратов и хранения в начислениях нет — 0, а не пропуск; выкуп — все проданы
    assert (D(i["returns"]), D(i["storage"]), i["facts"]["own"], i["facts"]["buyout_pct"]) == (0, 0, True, "100.0")
    assert d["tax"]["known"] is False and d["missing"] == [] and d["promo_title"] == "Распродажа"
    # α = 1 − 0,38 − 0,015 = 0,605; β = 200,08 + 60 = 260,08 → 429,884… и 260,08 / 0,405 = 642,172…
    r = d["result"]
    assert (r["min_price"], r["target_price"], r["profitable"]) == ("429.89", "642.18", True)
    # при 600 ₽: 0,605 × 600 − 260,08 = 102,92 ₽ → 17,15 %; в акции за 500: 302,5 − 260,08 = 42,42 → 8,48 %
    assert (r["current"]["profit"], r["current"]["margin"]) == ("102.92", "17.15")
    assert (r["promo"]["profit"], r["promo"]["margin"]) == ("42.42", "8.48")


async def test_what_if_prices(client, tokens, sessions):
    """«А если поставить …» (10.15): тот же расчёт при нескольких ценах — в порядке запроса."""
    p = await seed(sessions, await user_id_of(client, tokens))
    r = (await calc(client, tokens, p["A"].id, what_if=["700", "500", "429.89"]))["result"]
    # 0,605 × 700 − 260,08 = 163,42 → 23,35 %; 500 — как в акции; минимальная — ноль прибыли
    assert [(w["price"], w["profit"], w["margin"]) for w in r["what_if"]] == [
        ("700.00", "163.42", "23.35"), ("500.00", "42.42", "8.48"), ("429.89", "0.00", "0.00")]
    assert r["current"]["profit"] == "102.92"  # текущая цена — как была
    bad = await client.post("/api/v1/price/calculate", json={"product_id": str(p["A"].id), "what_if": ["0"]},
                            headers=auth(tokens))
    assert bad.status_code == 400  # ошибки проверки — понятным текстом, как везде (core/errors)


async def test_logistics_fallbacks_and_missing(client, tokens, sessions):
    p = await seed(sessions, await user_id_of(client, tokens))
    e = (await calc(client, tokens, p["E"].id))["inputs"]
    assert (D(e["logistics"]), e["logistics_source"]) == (D("80"), "product") and "90 дн." in e["logistics_text"]
    # C не продавался — средняя по магазину за 30 дней: (4 × 60 + 2 × 30) / 6 = 50 ₽
    c = (await calc(client, tokens, p["C"].id))["inputs"]
    assert (D(c["logistics"]), c["logistics_source"]) == (D("50"), "shop") and "оценка" in c["logistics_text"]
    # D без себестоимости — расчёта нет, сказано, что указать; своя себестоимость — расчёт есть
    d = await calc(client, tokens, p["D"].id)
    assert d["result"] is None and any("себестоимости" in m for m in d["missing"])
    d = await calc(client, tokens, p["D"].id, cost="164")
    assert d["result"]["min_price"] is not None and d["inputs"]["sources"]["cost"] == "ваше значение"


async def test_no_sales_at_all(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        product = Product(user_id=uid, ozon_product_id=9, offer_id="NEW", synced_at=now)
        db.add(product)
        await db.flush()
        db.add(ProductPrice(product_id=product.id, snapshot_at=now, price=D("500"), commission_pct=D("38")))
        db.add(ProductCost(user_id=uid, offer_id="NEW", cost_price=D("100"), effective_from=BEGINNING, source="manual",
                           created_at=now))
        await db.commit()
    d = await calc(client, tokens, product.id)
    # логистика неизвестна — не 0: расчёта нет
    assert d["result"] is None and d["inputs"]["logistics"] is None and d["inputs"]["logistics_text"] == "нет данных"
    assert any("логистик" in m for m in d["missing"])
    # указал логистику сам — посчитали: (100 × 1,22 + 40) / 0,62 = 261,29…
    d = await calc(client, tokens, product.id, logistics="40")
    assert d["inputs"]["logistics_source"] == "manual" and d["result"]["min_price"] == "261.30"


async def test_overrides_settings_and_tax(client, tokens, sessions):
    p = await seed(sessions, await user_id_of(client, tokens))
    # свои значения только для расчёта: реклама 6 %, маржа 30 %, другая цена
    d = await calc(client, tokens, p["A"].id, ad_pct="6", target_margin_pct="30", price="700", storage="5")
    # α = 0,605 − 0,06 = 0,545; β = 260,08 + 5 → 486,39…; 265,08 / 0,245 = 1081,95…
    assert (d["result"]["min_price"], d["result"]["target_price"]) == ("486.39", "1081.96")
    assert D(d["inputs"]["price"]) == 700 and d["inputs"]["sources"]["price"] == "ваше значение"
    # настройки по умолчанию → сохранённые
    s = (await client.get("/api/v1/settings", headers=auth(tokens))).json()
    assert (D(s["price_ad_pct"]), D(s["price_target_margin_pct"]), s["price_defaults"]) == (0, 20, True)
    r = await client.patch("/api/v1/settings", json={"price_ad_pct": "6", "price_target_margin_pct": "25"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["price_defaults"] is False
    # 4.2a: реклама по начислениям важнее настройки (настройка — когда данных нет); своя у товара — важнее факта
    d = await calc(client, tokens, p["A"].id)
    assert D(d["inputs"]["ad_pct"]) == 0 and d["result"]["min_price"] == "429.89"
    assert (await client.put(f"/api/v1/price/extras/{p['A'].id}", json={"ad_pct": "6"}, headers=auth(tokens))).status_code == 200
    d = await calc(client, tokens, p["A"].id)
    assert D(d["inputs"]["ad_pct"]) == 6 and d["result"]["min_price"] == "477.22"  # 260,08 / 0,545
    assert d["inputs"]["sources"]["ad_pct"] == "сохранено для товара" and D(d["inputs"]["saved"]["ad_pct"]) == 6
    bad = await client.patch("/api/v1/settings", json={"price_target_margin_pct": "95"}, headers=auth(tokens))
    assert bad.status_code == 400 and "Целевая маржа: не больше 90" in bad.json()["detail"]
    # налоговый профиль: УСН «Доходы» 6 % — доля в знаменателе: 260,08 / (0,545 − 0,06) = 536,25
    profile = {"legal_form": "ip", "regime": "usn_income"}
    assert (await client.put("/api/v1/tax-profile", json=profile, headers=auth(tokens))).status_code == 200
    d = await calc(client, tokens, p["A"].id)
    assert d["tax"]["title"] == "УСН «Доходы» 6 %" and d["tax"]["known"] and d["result"]["min_price"] == "536.25"
    # недостижимая цена — понятный текст, а не ошибка запроса
    d = await calc(client, tokens, p["A"].id, ad_pct="60")
    assert d["result"]["min_price"] is None and d["result"]["min_error"].startswith("Цена недостижима")


async def test_calculate_errors(client, tokens, sessions):
    p = await seed(sessions, await user_id_of(client, tokens))
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    r = await client.post("/api/v1/price/calculate", json={"product_id": str(p["A"].id)}, headers=auth(t2))
    assert r.status_code == 404 and r.json()["detail"] == "Товар не найден"
    r = await client.post("/api/v1/price/calculate", json={"product_id": str(p["A"].id), "logistics": "-1"}, headers=auth(tokens))
    assert r.status_code == 400 and "Логистика на 1 шт.: не меньше 0" in r.json()["detail"]
    assert (await client.post("/api/v1/price/calculate", json={"product_id": str(p["A"].id)})).status_code == 401


async def test_price_table_and_suggestions(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    await seed(sessions, uid)
    get = lambda **q: client.get("/api/v1/price", params=q, headers=auth(tokens))  # noqa: E731
    data = (await get()).json()
    rows = {r["offer_id"]: r for r in data["items"]}
    assert [r["offer_id"] for r in data["items"]] == ["ART-A", "ART-B", "ART-C", "ART-D", "ART-E"]
    # A: 600 ≥ мин. 429,89, ниже целевой 642,18; B: 400 ≥ мин. (200,08 + 30) / 0,605 = 380,30, ниже целевой
    a, b, c, d = rows["ART-A"], rows["ART-B"], rows["ART-C"], rows["ART-D"]
    assert (a["min_price"], a["target_price"], a["below_min"], a["below_target"]) == ("429.89", "642.18", False, True)
    assert a["promo"]["title"] == "Распродажа" and a["promo"]["margin"] == "8.48" and a["gap_pct"] == "39.6"
    assert (b["min_price"], b["below_min"], b["below_target"]) == ("380.30", False, True)
    assert (c["min_price"], c["logistics_source"], c["below_min"], c["below_target"]) == ("413.36", "shop", False, False)
    assert d["min_price"] is None and d["missing"] and d["margin"] is None
    assert data["counts"] == {"all": 5, "below_min": 0, "below_target": 3, "no_data": 1, "promo_loss": 0}
    assert data["settings"] == {"ad_pct": "0", "target_margin_pct": "20", "defaults": True}
    assert D(data["shop_logistics"]["per_unit"]) == 50 and data["tax"]["known"] is False
    # настройка «реклама» — только когда данных нет: продажи есть, рекламы в начислениях нет — 0 %
    await client.patch("/api/v1/settings", json={"price_ad_pct": "20"}, headers=auth(tokens))
    assert (await get()).json()["counts"]["below_min"] == 0
    # реклама без товара (оплата за клик по кампании) — 527,20 ₽ = 20 % продаж магазина за 30 дней (4 × 459 + 2 × 400)
    # делится по доле в продажах: α = 0,405 — ниже минимальной A (642,17), B (568,10) и E (691,56), акция A — в минус
    async with sessions() as db:
        db.add(Accrual(user_id=uid, type_name="Оплата за клик", accrual_date=today_msk() - timedelta(days=3),
                       amount=D("-527.20"), quantity=0, source="api", sync_scope="campaign:1", line_no=1))
        await db.commit()
    await pnl_cache.invalidate(uid)
    data = (await get()).json()
    assert D(data["shop_logistics"]["unattributed_ads_pct"]) == 20
    assert data["counts"]["below_min"] == 3 and data["counts"]["promo_loss"] == 1
    below = (await get(filters="below_min", sort="margin")).json()
    assert [r["offer_id"] for r in below["items"]] == ["ART-B", "ART-E", "ART-A"]  # хуже маржа — выше
    assert (await get(filters="no_data")).json()["items"][0]["offer_id"] == "ART-D"
    assert [r["offer_id"] for r in (await get(search="art-c")).json()["items"]] == ["ART-C"]
    assert [r["offer_id"] for r in (await get(sort="price", order="desc", limit=2)).json()["items"]] == ["ART-C", "ART-D"]
    assert (await get(filters="cheap")).status_code == 400
    s = (await client.get("/api/v1/price/suggestions", headers=auth(tokens))).json()
    assert [x["offer_id"] for x in s["loss"]] == ["ART-B", "ART-E", "ART-A"] and s["loss_count"] == 3
    assert s["loss"][0]["raise_to"] == s["loss"][0]["min_price"]
    # C: 1000 ₽ между минимальной 617,48 и целевой 250,08 / 0,205 = 1219,91
    assert [x["offer_id"] for x in s["underpriced"]] == ["ART-C"] and s["underpriced_count"] == 1
    assert (s["underpriced"][0]["raise_to"], s["underpriced"][0]["raise_pct"]) == ("1219.91", "22.0")
    assert s["underpriced"][0]["raise_to"] == s["underpriced"][0]["target_price"]


async def test_extra_expenses_shop_and_product(client, tokens, sessions):
    """Доп. расходы (правка 06.11.2026): % от цены для всех товаров — в «Настройках», у товара — свои хранение, прочее и
    % — сохраняются и попадают во все расчёты: калькулятор, таблицу цен."""
    p = await seed(sessions, await user_id_of(client, tokens))
    a = p["A"].id
    # 5 % для всех: α = 0,605 − 0,05 = 0,555 → мин. 260,08 / 0,555 = 468,61…; при 600 ₽: 333 − 260,08 = 72,92 → 12,15 %
    r = await client.patch("/api/v1/settings", json={"price_extra_pct": "5"}, headers=auth(tokens))
    assert r.status_code == 200 and D(r.json()["price_extra_pct"]) == 5
    d = await calc(client, tokens, a)
    assert D(d["inputs"]["extra_pct"]) == 5 and d["inputs"]["sources"]["extra_pct"] == "ваша настройка"
    assert d["result"]["min_price"] == "468.62" and d["result"]["current"]["profit"] == "72.92"
    assert d["result"]["current"]["extra"] == "30.00" and d["inputs"]["saved"] is None
    rows = {x["offer_id"]: x for x in (await client.get("/api/v1/price", headers=auth(tokens))).json()["items"]}
    assert rows["ART-A"]["min_price"] == "468.62"  # таблица — та же формула

    # у товара A свои: хранение 10 ₽, прочее 5 ₽, доп. расходы 0 % — вместо общих 5 %
    r = await client.put(f"/api/v1/price/extras/{a}", json={"storage": "10", "other": "5", "extra_pct": "0", "note": "коробка"},
                         headers=auth(tokens))
    saved = r.json()
    assert r.status_code == 200 and (D(saved["storage"]), D(saved["other"]), D(saved["extra_pct"]), saved["note"]) == (
        10, 5, 0, "коробка")
    d = await calc(client, tokens, a)
    i = d["inputs"]
    assert (D(i["storage"]), D(i["other"]), D(i["extra_pct"])) == (10, 5, 0)
    assert i["sources"]["storage"] == "сохранено для товара" and i["saved"]["note"] == "коробка"
    # β = 260,08 + 10 + 5 = 275,08; α = 0,605 → мин. 454,68…; при 600: 363 − 275,08 = 87,92
    assert d["result"]["min_price"] == "454.68" and d["result"]["current"]["profit"] == "87.92"
    rows = {x["offer_id"]: x for x in (await client.get("/api/v1/price", headers=auth(tokens))).json()["items"]}
    assert rows["ART-A"]["min_price"] == "454.68" and rows["ART-B"]["min_price"] != "380.30"  # у B — общие 5 %
    # своё значение в расчёте важнее сохранённого — только для этого расчёта
    assert (await calc(client, tokens, a, other="0"))["result"]["current"]["profit"] == "92.92"

    # пустые поля — убрать своё: снова общие 5 %
    r = await client.put(f"/api/v1/price/extras/{a}", json={}, headers=auth(tokens))
    assert r.status_code == 200 and r.json() is None
    assert (await calc(client, tokens, a))["result"]["min_price"] == "468.62"

    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    t2 = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    r = await client.put(f"/api/v1/price/extras/{a}", json={"other": "1"}, headers=auth(t2))
    assert r.status_code == 404
    r = await client.put(f"/api/v1/price/extras/{a}", json={"extra_pct": "95"}, headers=auth(tokens))
    assert r.status_code == 400
