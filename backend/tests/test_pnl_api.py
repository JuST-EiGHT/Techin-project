"""P&L за период (шаг 3.2): выбор источника по дням, пропуски, кэш, снимки закрытых месяцев."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.accrual import SOURCE_API, Accrual, AccrualDay, AccrualType
from app.models.pnl_snapshot import PnLSnapshot
from app.models.product import Product
from app.services import pnl_service
from app.services.pnl_stale import affected_period
from tests.test_report_pnl import MONTH, OWNER, WEEK, auth, import_reference_costs, set_profile, upload

WEEK_FROM, WEEK_TO = "2026-09-07", "2026-09-13"


async def pnl(client, tokens, date_from=WEEK_FROM, date_to=WEEK_TO, path="", **params):
    r = await client.get(
        f"/api/v1/pnl{path}", params={"date_from": date_from, "date_to": date_to, **params}, headers=auth(tokens)
    )
    assert r.status_code == 200, r.text
    return r.json()


async def user_id(client, tokens) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])


async def add_api_day(sessions, uid, day: date, lines: list[tuple[str, str, int | None, str | None]]):
    """Начисления из API за день: (тип, сумма, шт., артикул товара)."""
    async with sessions() as db:
        types = {t.name_ru: t.id for t in (await db.execute(select(AccrualType))).scalars()}
        products = {p.offer_id: p.id for p in (await db.execute(select(Product))).scalars()}
        for i, (type_name, amount, qty, offer) in enumerate(lines):
            if type_name not in types:
                types[type_name] = 900 + len(types)
                db.add(AccrualType(id=types[type_name], name_ru=type_name))
            if offer and offer not in products:
                products[offer] = uuid.uuid4()
                db.add(Product(
                    id=products[offer], user_id=uid, ozon_product_id=len(products), ozon_sku=1000 + len(products),
                    offer_id=offer, name=f"Товар {offer}", synced_at=datetime.now(timezone.utc),
                ))
            await db.flush()
            db.add(Accrual(
                user_id=uid, product_id=products.get(offer), accrual_type_id=types[type_name], accrual_date=day,
                amount=Decimal(amount), quantity=qty, source=SOURCE_API, sync_scope=f"day:{day}", line_no=i,
            ))
        db.add(AccrualDay(user_id=uid, day=day, synced_at=datetime.now(timezone.utc), entries=len(lines)))
        await db.commit()


async def test_week_from_xlsx_lines_matches_report(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    report = await upload(client, tokens, WEEK)
    body = await pnl(client, tokens)
    assert body["data_source"] == "xlsx" and body["source"] == "auto"
    assert body["coverage"] == {"days_total": 7, "xlsx_days": 7, "api_days": 0, "missing_days": 0, "missing": []}
    assert [r["id"] for r in body["reports"]] == [report["id"]]
    # из строк по дням — те же цифры, что у отчёта целиком (BUSINESS_LOGIC §3)
    assert body["auto_total"] == "10955.83"
    p, rp = body["pnl"], report["pnl"]
    for key in ("effective_total", "owed", "profit_before_tax", "tax", "vat", "net", "margin", "tax_title"):
        assert p[key] == rp[key], key
    assert (p["owed"], p["net"]) == ("12374.90", "-1419.07")
    assert body["by_type"] == report["summary"]["by_type"]
    assert body["from_snapshot"] is False and not body["missing_cost"]


async def test_missing_days_are_gaps_not_zero(client, tokens):
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    body = await pnl(client, tokens, "2026-09-01", "2026-09-30")
    assert body["coverage"]["xlsx_days"] == 7 and body["coverage"]["missing_days"] == 23
    assert body["coverage"]["missing"] == [
        {"date_from": "2026-09-01", "date_to": "2026-09-06"},
        {"date_from": "2026-09-14", "date_to": "2026-09-30"},
    ]
    assert any(w.startswith("Нет данных за 23 дн.: 01.09.2026–06.09.2026, 14.09.2026–30.09.2026") for w in body["warnings"])
    # без себестоимости — список артикулов, а не 0
    assert body["missing_cost"] and body["pnl"]["owed"] == "0.00"

    days = (await pnl(client, tokens, "2026-09-05", "2026-09-08", path="/by-day"))["days"]
    assert [d["source"] for d in days] == [None, None, "xlsx", "xlsx"]
    assert days[0] == {"date": "2026-09-05", "source": None, "revenue": None, "total": None, "owed": None,
                       "profit_before_tax": None}
    week = (await pnl(client, tokens, path="/by-day"))["days"]
    assert sum(Decimal(d["total"]) for d in week) == Decimal("10955.83")


async def test_nothing_for_period(client, tokens):
    await upload(client, tokens, MONTH)  # отчёт о реализации по дням не делится — в P&L периода не входит
    body = await pnl(client, tokens, "2026-08-01", "2026-08-31")
    assert body["data_source"] == "none" and body["pnl"] is None and body["auto_total"] is None
    assert [w.split(" ")[0:4] for w in body["warnings"]] == [
        ["Отчёт", "о", "реализации", "01.08.2026–31.08.2026"], ["За", "период", "нет", "данных"]
    ]


async def test_auto_xlsx_and_api_sources(client, tokens, sessions):
    await set_profile(client, tokens, OWNER)
    await client.put("/api/v1/costs/A-1", json={"cost_price": "100", "vat_multiplier": "1"}, headers=auth(tokens))
    await upload(client, tokens, WEEK)
    uid = await user_id(client, tokens)
    # API: день внутри недели отчёта (его заменит XLSX) и день после неё
    await add_api_day(sessions, uid, date(2026, 9, 10), [("Выручка", "1", 1, "A-1")])
    await add_api_day(sessions, uid, date(2026, 9, 14), [
        ("Выручка", "1000", 2, "A-1"), ("Вознаграждение за продажу", "-150", None, "A-1"),
        ("Подписка Premium", "-50", None, None),
    ])

    auto = await pnl(client, tokens, WEEK_FROM, "2026-09-14")
    assert auto["data_source"] == "mixed"
    assert (auto["coverage"]["xlsx_days"], auto["coverage"]["api_days"]) == (7, 1)
    assert auto["auto_total"] == str(Decimal("10955.83") + 800)
    assert any(w.startswith("По данным Ozon API — 1 дн. из 8:") for w in auto["warnings"])

    api = await pnl(client, tokens, WEEK_FROM, "2026-09-14", source="api")
    assert api["data_source"] == "api" and api["auto_total"] == "801.00"
    assert api["coverage"]["missing_days"] == 6 and api["pnl"]["data_source"] == "api"
    assert api["by_type"] == {"Выручка": "1001.00", "Вознаграждение за продажу": "-150.00", "Подписка Premium": "-50.00"}
    # себестоимость — по строкам «Выручка» с товаром: 3 шт. × 100 без НДС поставщика
    assert api["pnl"]["owed"] == "300.00" and api["pnl"]["profit_before_tax"] == "501.00"
    assert any(w.startswith("По данным Ozon API — 2 дн. из 8:") for w in api["warnings"])

    xlsx = await pnl(client, tokens, WEEK_FROM, "2026-09-14", source="xlsx")
    assert xlsx["data_source"] == "xlsx" and xlsx["auto_total"] == "10955.83"
    assert xlsx["coverage"]["missing"] == [{"date_from": "2026-09-14", "date_to": "2026-09-14"}]

    day = (await pnl(client, tokens, "2026-09-14", "2026-09-14", path="/by-day"))["days"][0]
    assert day == {"date": "2026-09-14", "source": "api", "revenue": "1000.00", "total": "800.00", "owed": "200.00",
                   "profit_before_tax": "600.00"}


async def test_period_params_and_manual_total(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    report = await upload(client, tokens, WEEK)
    p = (await pnl(client, tokens, manual_total="12000", tax_rate="0.06", tax_base="revenue",
                   vat_multiplier="1"))["pnl"]
    assert p["effective_total"] == "12000.00" and p["supplier_vat"] == "1.000"
    assert p["tax_title"] == "Свой процент: 6 % от дохода — параметр расчёта"
    # ручная сумма отчёта, целиком вошедшего в период, учитывается в P&L периода
    await client.patch(f"/api/v1/reports/{report['id']}", json={"manual_total": "11000"}, headers=auth(tokens))
    body = await pnl(client, tokens, "2026-09-01", "2026-09-30")
    assert body["pnl"]["effective_total"] == "11000.00" and body["reports"][0]["manual_total_applied"] is True
    assert any(w.startswith("Учтены ручные суммы") for w in body["warnings"])
    # отчёт задет периодом лишь частично — его ручная сумма не применяется
    part = await pnl(client, tokens, "2026-09-10", "2026-09-30")
    assert part["reports"][0]["manual_total_applied"] is False


async def test_cache_and_reset_on_changes(client, tokens, sessions):
    await set_profile(client, tokens, OWNER)
    first = await pnl(client, tokens)
    assert first["data_source"] == "none"
    # данные изменились в обход сервиса — 5 минут отдаётся кэш
    uid = await user_id(client, tokens)
    await add_api_day(sessions, uid, date(2026, 9, 8), [("Выручка", "500", 1, "A-1")])
    assert (await pnl(client, tokens))["calculated_at"] == first["calculated_at"]
    # загрузка отчёта сбрасывает кэш
    await upload(client, tokens, WEEK)
    assert (await pnl(client, tokens))["data_source"] == "xlsx"
    # себестоимость, налоговый профиль, правка и удаление отчёта — тоже
    before = await pnl(client, tokens)
    await client.put("/api/v1/costs/6001-2RS.C3.SRL.NAC.TH", json={"cost_price": "164"}, headers=auth(tokens))
    after = await pnl(client, tokens)
    assert after["pnl"]["owed"] != before["pnl"]["owed"]
    await set_profile(client, tokens, {**OWNER, "rate_pct": "10", "effective_from": "2026-09-01"})
    assert (await pnl(client, tokens))["pnl"]["tax_title"] == "Свой процент: 10 % от прибыли"
    report_id = (await client.get("/api/v1/reports", headers=auth(tokens))).json()["items"][0]["id"]
    await client.delete(f"/api/v1/reports/{report_id}", headers=auth(tokens))
    assert (await pnl(client, tokens))["data_source"] == "api"  # остались только данные API
    await client.post(f"/api/v1/reports/{report_id}/restore", headers=auth(tokens))
    assert (await pnl(client, tokens))["data_source"] == "xlsx"


async def test_snapshot_of_closed_month(client, tokens, sessions):
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    month = ("2026-09-01", "2026-09-30")
    assert pnl_service.is_closed_month(date(2026, 9, 1), date(2026, 9, 30))
    first = await pnl(client, tokens, *month)
    assert first["from_snapshot"] is False
    async with sessions() as db:
        snap = (await db.execute(select(PnLSnapshot))).scalar_one()
        assert (snap.date_from, snap.date_to, snap.source, snap.stale) == (date(2026, 9, 1), date(2026, 9, 30), "auto", False)
        assert snap.data_json["auto_total"] == first["auto_total"]
    # с параметрами расчёта и не за месяц — без снимка
    await pnl(client, tokens, *month, tax_rate="0.06")
    await pnl(client, tokens, "2026-09-01", "2026-09-29")
    async with sessions() as db:
        assert len((await db.execute(select(PnLSnapshot))).scalars().all()) == 1

    periods = (await client.get("/api/v1/pnl/periods", headers=auth(tokens))).json()
    sep = next(m for m in periods["months"] if m["month"] == "2026-09")
    assert sep["snapshot"]["stale"] is False and sep["closed"] is True

    # себестоимость с октября сентябрь не задевает — снимок свежий; новый профиль с 15.09 — устарел
    await client.put("/api/v1/costs/X-1", json={"cost_price": "10"}, headers=auth(tokens))  # первая — с начала учёта
    await client.put(
        "/api/v1/costs/X-1", json={"cost_price": "20", "effective_from": "2026-10-01"}, headers=auth(tokens)
    )
    async with sessions() as db:
        snap = (await db.execute(select(PnLSnapshot))).scalar_one()
        assert snap.stale is True and snap.stale_reason == "Себестоимость X-1"  # от первой цены X-1
    second = await pnl(client, tokens, *month)  # пересчитан
    assert second["from_snapshot"] is False
    third = await pnl(client, tokens, *month)
    assert third == second  # из кэша
    pnl_service.cache.clear_memory()
    from_db = await pnl(client, tokens, *month)
    assert from_db["from_snapshot"] is True and from_db["auto_total"] == first["auto_total"]

    await client.put(
        "/api/v1/costs/X-1", json={"cost_price": "30", "effective_from": "2026-10-05"}, headers=auth(tokens)
    )
    async with sessions() as db:
        assert (await db.execute(select(PnLSnapshot))).scalar_one().stale is False  # октябрь — не сентябрь
    await set_profile(client, tokens, {**OWNER, "rate_pct": "15", "effective_from": "2026-09-15"})
    async with sessions() as db:
        snap = (await db.execute(select(PnLSnapshot))).scalar_one()
        assert snap.stale is True and snap.stale_reason == "Изменён налоговый профиль"


def test_affected_period():
    d = date
    assert affected_period([], d(2026, 9, 1)) == (None, None)  # первая запись действует и раньше себя
    assert affected_period([d(2000, 1, 1)], d(2026, 9, 1)) == (d(2026, 9, 1), None)
    assert affected_period([d(2026, 10, 1)], d(2026, 9, 1)) == (None, d(2026, 9, 30))
    assert affected_period([d(2026, 1, 1), d(2026, 10, 1)], d(2026, 9, 1)) == (d(2026, 9, 1), d(2026, 9, 30))


async def test_periods(client, tokens, sessions):
    empty = (await client.get("/api/v1/pnl/periods", headers=auth(tokens))).json()
    assert empty == {"reports": [], "api": [], "first_day": None, "last_day": None, "months": []}
    await upload(client, tokens, WEEK)
    uid = await user_id(client, tokens)
    for day in (14, 15, 20):
        await add_api_day(sessions, uid, date(2026, 9, day), [("Выручка", "1", 1, None)])
    body = (await client.get("/api/v1/pnl/periods", headers=auth(tokens))).json()
    assert [(r["report_type"], r["period_start"]) for r in body["reports"]] == [("accrual", "2026-09-07")]
    assert body["api"] == [
        {"date_from": "2026-09-14", "date_to": "2026-09-15"}, {"date_from": "2026-09-20", "date_to": "2026-09-20"}
    ]
    assert (body["first_day"], body["last_day"]) == ("2026-09-07", "2026-09-20")
    assert body["months"][-1] == {
        "month": "2026-09", "date_from": "2026-09-01", "date_to": "2026-09-30", "closed": True,
        "xlsx_days": 7, "api_days": 3, "missing_days": 20, "snapshot": None,
    }
    assert body["months"][0]["month"] == pnl_service.today_msk().strftime("%Y-%m")


async def test_validation(client, tokens):
    async def bad(**params):
        r = await client.get("/api/v1/pnl", params=params, headers=auth(tokens))
        assert r.status_code == 400, r.text
        return r.json()["detail"]

    assert await bad(date_from="2026-09-30", date_to="2026-09-01") == "Период: дата начала позже даты окончания"
    assert await bad(date_from="2025-01-01", date_to="2026-09-01") == "Период: не больше 366 дней за раз"
    assert await bad(source="cabinet") == "Источник данных: недопустимое значение"
    assert await bad(vat_multiplier="3") == "Коэффициент НДС: не больше 2"
    assert await bad(tax_rate="abc") == "Ставка налога: нужно число"
    assert (await bad(tax_base="revenue")).startswith("Налог считается: укажите и ставку налога")
    assert await bad(date_from="01.09.2026") == "Дата начала: дата в формате ГГГГ-ММ-ДД"
    assert (await client.get("/api/v1/pnl")).status_code == 401
    # без дат — с начала текущего месяца по сегодня
    body = (await client.get("/api/v1/pnl", headers=auth(tokens))).json()
    today = pnl_service.today_msk()
    assert (body["date_from"], body["date_to"]) == (today.replace(day=1).isoformat(), today.isoformat())


async def test_other_users_data_is_invisible(client, tokens):
    await upload(client, tokens, WEEK)
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    r = await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})
    other = r.json()
    body = await pnl(client, other)
    assert body["data_source"] == "none" and body["reports"] == []
    periods = (await client.get("/api/v1/pnl/periods", headers=auth(other))).json()
    assert periods["reports"] == [] and periods["months"] == []
