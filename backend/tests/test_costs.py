"""Справочник себестоимости (шаг 2.3): история цен, цена на дату отчёта, импорт, пересчёт отчётов."""

import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import openpyxl
from sqlalchemy import func, select

from app.models.product import Product
from app.models.product_cost import ProductCost
from app.services import costs as costs_svc
from app.services.costs import CostHistory
from tests.conftest import REFERENCE_COSTS_CSV

FIXTURES = Path(__file__).parent / "fixtures"
ACCRUAL = "accrual_2026-09-07_13.xlsx"  # период 07.09–13.09.2026
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ART = "6001-2RS.C3.SRL.NAC.TH"  # в отчёте 2 шт., эталонная себестоимость 164


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def rec(cost: str, day: date, created_sec: int = 0, offer: str = ART) -> ProductCost:
    return ProductCost(
        offer_id=offer,
        cost_price=Decimal(cost),
        effective_from=day,
        created_at=datetime(2026, 10, 1, 0, 0, created_sec, tzinfo=timezone.utc),
    )


def xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def import_file(client, tokens, name: str, content: bytes, dry_run: bool = False):
    return await client.post(
        "/api/v1/costs/import",
        params={"dry_run": dry_run},
        headers=auth(tokens),
        files={"file": (name, content, "text/csv" if name.endswith(".csv") else XLSX_TYPE)},
    )


async def upload_report(client, tokens) -> dict:
    r = await client.post(
        "/api/v1/reports/upload",
        headers=auth(tokens),
        files={"file": (ACCRUAL, (FIXTURES / ACCRUAL).read_bytes(), XLSX_TYPE)},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def get_report(client, tokens, report_id: str) -> dict:
    return (await client.get(f"/api/v1/reports/{report_id}", headers=auth(tokens))).json()


# ---------- история цен ----------


def test_cost_on_date_uses_history_and_first_record_before_it():
    history = CostHistory(
        [rec("150", date(2026, 10, 1)), rec("100", date(2026, 9, 1)), rec("160", date(2026, 10, 1), created_sec=5)],
        today=date(2026, 10, 13),
    )
    assert history.at(date(2026, 9, 7)).cost_of(ART) == Decimal("100")
    assert history.at(date(2026, 10, 1)).cost_of(ART) == Decimal("160")  # та же дата — позже введённая
    assert history.at(date(2026, 1, 1)).cost_of(ART) == Decimal("100")  # раньше первой записи — первая
    assert history.cost_of(ART) == Decimal("160") and history.cost_of("UNKNOWN") is None  # неизвестный — не 0
    assert len(history.history(ART)) == 3


# ---------- правка в ячейке и история ----------


async def test_put_cost_creates_history_and_validates(client, tokens):
    put = lambda body, offer=ART: client.put(f"/api/v1/costs/{offer}", json=body, headers=auth(tokens))  # noqa: E731

    r = await put({"cost_price": "164", "vat_multiplier": "1.22", "note": "поставка 1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["item"]["cost_price"] == "164.00"
    assert (body["item"]["vat_multiplier"], body["item"]["note"], body["item"]["history_count"]) == (
        "1.220",
        "поставка 1",
        1,
    )

    # та же цена с той же даты — без новой записи; новая цена — новая запись, старая остаётся
    assert (await put({"cost_price": "164.00", "vat_multiplier": "1.22", "note": "поставка 1"})).json()[
        "created"
    ] is False
    body = (await put({"cost_price": "170", "effective_from": "2026-11-01"})).json()
    assert body["created"] is True
    history = (await client.get(f"/api/v1/costs/{ART}/history", headers=auth(tokens))).json()
    assert [(h["cost_price"], h["effective_from"]) for h in history][:2] == [
        ("170.00", "2026-11-01"),
        ("164.00", history[1]["effective_from"]),
    ]

    # артикул со слэшем и точками — путь целиком
    assert (await put({"cost_price": "10"}, offer="ABC/12.3")).status_code == 200
    assert (await client.get("/api/v1/costs/ABC/12.3/history", headers=auth(tokens))).status_code == 200
    r = await client.get("/api/v1/costs/NOPE/history", headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (404, "Себестоимость для этого артикула ещё не задавалась")

    for bad, text in [
        ({"cost_price": "0"}, "Себестоимость: больше 0"),
        ({"cost_price": "-5"}, "Себестоимость: больше 0"),
        ({"cost_price": "abc"}, "Себестоимость: нужно число"),
        ({"cost_price": "10", "vat_multiplier": "2.5"}, "Коэффициент НДС: не больше 2"),
        ({"cost_price": "10", "vat_multiplier": "0.9"}, "Коэффициент НДС: не меньше 1"),
        ({}, "Себестоимость: обязательное поле"),
    ]:
        r = await put(bad)
        assert (r.status_code, r.json()["detail"]) == (400, text), bad
    assert (await client.put(f"/api/v1/costs/{ART}", json={"cost_price": "1"})).status_code == 401


async def test_list_shows_catalog_report_and_missing_articles(client, tokens, sessions):
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    async with sessions() as db:
        db.add(
            Product(
                user_id=uuid.UUID(me["id"]),
                ozon_product_id=1,
                ozon_sku=777,
                offer_id="CATALOG-ONLY",
                name="Товар из каталога",
                synced_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    await upload_report(client, tokens)
    await client.put(f"/api/v1/costs/{ART}", json={"cost_price": "164"}, headers=auth(tokens))

    page = (await client.get("/api/v1/costs", headers=auth(tokens))).json()
    rows = {r["offer_id"]: r for r in page["items"]}
    assert page["total"] == 17 and page["missing_count"] == 16  # 16 артикулов отчёта + 1 из каталога
    assert rows[ART]["cost_price"] == "164.00" and rows[ART]["report_qty"] == 2
    assert rows["CATALOG-ONLY"]["in_catalog"] and rows["CATALOG-ONLY"]["cost_price"] is None
    assert rows["6004-2RS.CM.SRL.NAC.TH"]["report_qty"] == 19 and rows["6004-2RS.CM.SRL.NAC.TH"]["name"]
    # без себестоимости — первыми, самые продаваемые наверху
    assert page["items"][0]["offer_id"] == "6004-2RS.CM.SRL.NAC.TH"

    missing = (await client.get("/api/v1/costs", params={"missing": True}, headers=auth(tokens))).json()
    assert missing["total"] == 16 and all(r["cost_price"] is None for r in missing["items"])
    found = (await client.get("/api/v1/costs", params={"search": "каталог"}, headers=auth(tokens))).json()
    assert [r["offer_id"] for r in found["items"]] == ["CATALOG-ONLY"]


# ---------- импорт ----------


async def test_import_reference_csv_preview_apply_and_repeat(client, tokens, sessions):
    content = REFERENCE_COSTS_CSV.read_bytes()
    preview = (await import_file(client, tokens, "cost_prices.csv", content, dry_run=True)).json()
    assert preview["applied"] is False
    assert preview["counts"] == {"new": 78, "changed": 0, "unchanged": 0, "skipped": 0, "error": 0}
    async with sessions() as db:
        assert (await db.execute(select(func.count()).select_from(ProductCost))).scalar_one() == 0  # предпросмотр

    applied = (await import_file(client, tokens, "cost_prices.csv", content)).json()
    assert applied["applied"] is True and applied["counts"]["new"] == 78
    row = next(r for r in applied["rows"] if r["offer_id"] == ART)
    assert (row["cost_price"], row["vat_multiplier"], row["note"]) == ("164.00", "1.220", "перенесено из калькулятора")

    again = (await import_file(client, tokens, "cost_prices.csv", content)).json()
    assert again["counts"] == {"new": 0, "changed": 0, "unchanged": 78, "skipped": 0, "error": 0}  # без дублей
    async with sessions() as db:
        assert (await db.execute(select(func.count()).select_from(ProductCost))).scalar_one() == 78


async def test_import_xlsx_russian_headers_cp1251_csv_and_row_errors(client, tokens):
    # заголовки по-русски, таблица не с первой строки, «без НДС» в названии себестоимости — не колонка НДС
    content = xlsx(
        [
            ["Себестоимость товаров"],
            [],
            ["Артикул", "Себестоимость без НДС, ₽", "Коэффициент НДС", "Комментарий", "Действует с"],
            ["A-1", "1 250,50", "1,22", "партия 5", "01.09.2026"],
            ["A-2", 99, None, None, None],
            ["A-3", 0, None, None, None],
            ["", 10, None, None, None],
            ["A-2", 50, None, None, None],
            ["A-4", 10, 3, None, None],
            ["A-5", "дорого", None, None, None],
            ["A-6", 10, None, None, "вчера"],
            ["A-7", None, "1,22", "цену не знаю", None],
        ]
    )
    body = (await import_file(client, tokens, "costs.xlsx", content)).json()
    assert body["counts"] == {"new": 2, "changed": 0, "unchanged": 0, "skipped": 1, "error": 6}
    rows = {r["line"]: r for r in body["rows"]}
    assert (rows[4]["cost_price"], rows[4]["vat_multiplier"], rows[4]["effective_from"]) == (
        "1250.50",
        "1.220",
        "2026-09-01",
    )
    assert rows[5]["vat_multiplier"] is None  # не указан — как в налоговом профиле
    assert rows[6]["message"] == "Себестоимость должна быть больше 0"
    assert rows[7]["message"] == "Не указан артикул"
    assert rows[8]["message"] == "Артикул уже есть в строке 5"
    assert rows[9]["message"] == "Ставка НДС: нужно 22 %, 20 %, 10 %, 7 %, 5 %, 0 % или «без НДС» (можно и 1,22)"
    assert rows[10]["message"] == "Себестоимость: «дорого» — не число"
    assert rows[11]["message"] == "«Действует с»: нужна дата ДД.ММ.ГГГГ, например 01.09.2026"
    # пустая себестоимость — не ошибка: строку шаблона просто не заполнили
    assert (rows[12]["status"], rows[12]["message"]) == ("skipped", "Себестоимость не указана — строка пропущена")

    # CSV из Excel: Windows-1251 и «;»; изменившаяся цена — changed, новая запись
    csv_1251 = "Артикул;Себестоимость\nA-1;1300\nA-7;5,5\n".encode("cp1251")
    body = (await import_file(client, tokens, "costs.csv", csv_1251)).json()
    assert body["counts"] == {"new": 1, "changed": 1, "unchanged": 0, "skipped": 0, "error": 0}
    assert next(r for r in body["rows"] if r["offer_id"] == "A-1")["current_cost"] == "1250.50"


async def test_import_file_errors(client, tokens):
    for name, content, text in [
        ("costs.csv", "Товар;Цена продажи\nA;1\n".encode(), "Не найдены колонки «Артикул» и «Себестоимость»"),
        ("costs.xls", b"\xd0\xcf\x11\xe0" + b"\0" * 100, "Файл в старом формате Excel (.xls)"),
        ("costs.xlsx", b"PK\x03\x04 broken", "Не удалось открыть файл Excel"),
        ("costs.csv", b"", "Файл пустой."),
    ]:
        r = await import_file(client, tokens, name, content)
        assert r.status_code == 400 and r.json()["detail"].startswith(text), (name, r.text)


# ---------- себестоимость в отчётах ----------


async def test_reports_pick_up_costs_and_old_reports_keep_old_price(client, tokens):
    report = await upload_report(client, tokens)
    assert report["cost_sales"] == "0.00" and report["missing_cost_count"] == 16  # справочник пуст — не 0 × qty
    assert report["summary"]["warnings"][0].startswith("Не найдена себестоимость для 16 артикулов")

    applied = (await import_file(client, tokens, "cost_prices.csv", REFERENCE_COSTS_CSV.read_bytes())).json()
    assert applied["recosted_reports"] == 1
    report = await get_report(client, tokens, report["id"])
    assert (report["cost_sales"], report["missing_cost_count"]) == ("10143.36", 0)  # эталон BUSINESS_LOGIC §3
    assert not any(w.startswith("Не найдена себестоимость") for w in report["summary"]["warnings"])

    # новая цена с 01.10.2026 — сентябрьский отчёт не меняется (прошлые P&L стабильны)
    r = await client.put(
        f"/api/v1/costs/{ART}", json={"cost_price": "200", "effective_from": "2026-10-01"}, headers=auth(tokens)
    )
    assert r.json()["recosted_reports"] == 0
    assert (await get_report(client, tokens, report["id"]))["cost_sales"] == "10143.36"
    # цена, действующая с начала периода отчёта, — меняет его: 10143.36 − 164×2 + 180×2
    r = await client.put(
        f"/api/v1/costs/{ART}", json={"cost_price": "180", "effective_from": "2026-09-01"}, headers=auth(tokens)
    )
    assert r.json()["recosted_reports"] == 1
    report = await get_report(client, tokens, report["id"])
    assert report["cost_sales"] == "10175.36"
    assert report["summary"]["by_article"][ART]["cost"] == "180.00"

    # перечитывание файла даёт ту же себестоимость на дату отчёта
    r = await client.post(f"/api/v1/reports/{report['id']}/reparse", headers=auth(tokens))
    assert r.json()["cost_sales"] == "10175.36"


async def test_parse_preview_uses_user_costs(client, tokens):
    await client.put(f"/api/v1/costs/{ART}", json={"cost_price": "164"}, headers=auth(tokens))
    r = await client.post(
        "/api/v1/reports/parse",
        headers=auth(tokens),
        files={"file": (ACCRUAL, (FIXTURES / ACCRUAL).read_bytes(), XLSX_TYPE)},
    )
    body = r.json()
    assert body["cost_sales"] == "328.00" and len(body["missing_cost"]) == 15


async def test_users_do_not_see_each_others_costs(client, tokens):
    await client.put(f"/api/v1/costs/{ART}", json={"cost_price": "164"}, headers=auth(tokens))
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (
        await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})
    ).json()
    assert (await client.get("/api/v1/costs", headers=auth(other))).json()["total"] == 0
    assert (await client.get(f"/api/v1/costs/{ART}/history", headers=auth(other))).status_code == 404


async def test_cost_prices_for_promotions(client, tokens, sessions):
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    user_id = uuid.UUID(me["id"])
    async with sessions() as db:
        product = Product(
            user_id=user_id, ozon_product_id=5, ozon_sku=1, offer_id=ART, synced_at=datetime.now(timezone.utc)
        )
        db.add_all(
            [
                product,
                Product(user_id=user_id, ozon_product_id=6, offer_id="NO-COST", synced_at=datetime.now(timezone.utc)),
            ]
        )
        await db.commit()
    await client.put(f"/api/v1/costs/{ART}", json={"cost_price": "164"}, headers=auth(tokens))
    async with sessions() as db:  # НДС поставщика не указан, профиля нет — общая ставка 22 %
        assert await costs_svc.cost_prices_with_vat(db, user_id) == {product.id: Decimal("200.08")}
    profile = {"legal_form": "ip", "regime": "usn_income", "supplier_vat_pct": "0"}  # поставщик без НДС
    assert (await client.put("/api/v1/tax-profile", json=profile, headers=auth(tokens))).status_code == 200
    async with sessions() as db:
        assert await costs_svc.cost_prices_with_vat(db, user_id) == {product.id: Decimal("164.00")}
    await client.put(f"/api/v1/costs/{ART}", json={"cost_price": "164", "vat_multiplier": "1.1"}, headers=auth(tokens))
    async with sessions() as db:  # своя ставка в записи себестоимости важнее профиля
        assert await costs_svc.cost_prices_with_vat(db, user_id) == {product.id: Decimal("180.40")}

