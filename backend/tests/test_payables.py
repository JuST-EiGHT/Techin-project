"""Долги поставщикам (шаг 10.29): расчёт кредиторской задолженности, доступ только при постоплате, начало учёта, закупки с
отсрочкой, оплаты (внесённые и из «Операций»), версии записей, шаблон и загрузка списком."""
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import select

from app.api.v1 import payables as payables_api
from app.api.v1 import plan_calendar as plan_api
from app.api.v1 import planning as planning_api
from app.engine import payables as eng
from app.models.accrual import SOURCE_API, Accrual, AccrualType
from app.models.product import Product
from app.services import payables, plan_calendar, planning, pnl_service, tax_profile
from tests.test_plan_calendar import OFFER, TODAY, add_sales, days, setup
from tests.test_pnl_api import user_id
from tests.test_report_pnl import auth, import_reference_costs

D = Decimal
URL = "/api/v1/finance/payables"
DAILY = D("2076.44")  # 2 шт. в день: себестоимость 851 ₽ × НДС 22 % × 2


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for module in (planning, planning_api, plan_calendar, plan_api, tax_profile, pnl_service, payables, payables_api):
        monkeypatch.setattr(module, "today_msk", lambda: TODAY)


async def call(client, tokens, method, path="", expect=200, **kw):
    r = await client.request(method, f"{URL}{path}", headers=auth(tokens), **kw)
    assert r.status_code == expect, r.text
    return r.json() if r.content else None


async def set_supplier(client, tokens, payment, days_=None):
    body = {"supplier_payment": payment}
    if days_:
        body["supplier_deferral_days"] = days_
    r = await client.patch("/api/v1/settings", json=body, headers=auth(tokens))
    assert r.status_code == 200, r.text


async def start(client, tokens, day="2026-09-28", amount="10000", **kw):
    return await call(client, tokens, "PUT", "/setup", json={"start": day, "amount": amount, **kw})


# ---------- расчётный модуль ----------


def test_sale_period_week_and_month():
    assert eng.sale_period(date(2026, 10, 14), "week", 1) == (date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 19))
    assert eng.sale_period(date(2026, 10, 14), "week", 5) == (date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 23))
    assert eng.sale_period(date(2026, 10, 14), "month", 10) == (date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 10))
    assert eng.sale_period(date(2026, 12, 3), "month", 31)[2] == date(2027, 1, 31)  # 31-е — последний день месяца
    assert eng.sale_period(date(2027, 1, 3), "month", 31)[2] == date(2027, 2, 28)


def acc(day, due, amount, kind="sale"):
    return eng.Accrual(day, due, D(amount), kind, kind)


def test_settle_fifo_overdue_buckets_and_prepaid():
    today = date(2026, 10, 14)
    accruals = [
        acc(date(2026, 8, 1), date(2026, 8, 15), "1000"),   # 60 дней просрочки
        acc(date(2026, 9, 1), date(2026, 9, 20), "2000"),   # 24 дня
        acc(date(2026, 10, 1), date(2026, 10, 14), "500"),  # срок сегодня — не просрочен
        acc(date(2026, 10, 10), date(2026, 10, 20), "700"),  # через 6 дней
        acc(date(2026, 10, 12), date(2026, 11, 20), "300"),  # через 37 дней
        acc(date(2026, 7, 1), None, "400", "opening"),       # срока нет
    ]
    # платёж 1 200: сначала закрывает самое раннее — долг без срока и первое начисление
    led = eng.settle(accruals, [eng.Payment(date(2026, 10, 1), D("1200"), "manual")], today)
    assert led.accrued == D("4900.00") and led.paid == D("1200.00") and led.prepaid == 0
    assert led.debt == D("3700.00")
    # opening (по дате начисления — первый) 400 закрыт, из 1 000 остаток 200 — просрочка 60 дней
    assert [(i.accrual.kind, i.remaining, i.overdue_days) for i in led.items][:2] == [("sale", D("200.00"), 60), ("sale", D("2000.00"), 24)]
    assert led.overdue == D("2200.00") and led.not_due == D("1500.00")
    assert led.buckets == {"no_term": 0, "not_due": D("1500.00"), "d1_30": D("2000.00"), "d31_60": D("200.00"), "d61_plus": 0}
    assert led.due_in[7] == D("1200.00") and led.due_in[14] == D("1200.00") and led.due_in[30] == D("1200.00")
    # платят больше, чем начислено, — аванс, а не отрицательный долг
    led = eng.settle(accruals, [eng.Payment(date(2026, 10, 1), D("6000"), "manual")], today)
    assert led.debt == 0 and led.prepaid == D("1100.00") and led.items == []
    # возврат денег от поставщика (минус в оплатах) закрывать нечем: долг — всё начисленное
    led = eng.settle(accruals, [eng.Payment(date(2026, 10, 1), D("-100"), "operation")], today)
    assert led.debt == led.accrued == D("4900.00") and led.paid == D("-100.00")


def test_series_cumulative():
    a = [acc(date(2026, 10, 1), None, "100", "opening"), acc(date(2026, 10, 3), date(2026, 10, 9), "50")]
    p = [eng.Payment(date(2026, 10, 3), D("30"), "manual")]
    out = eng.series(a, p, date(2026, 10, 2), date(2026, 10, 4))
    assert out == [(date(2026, 10, 2), D("100.00")), (date(2026, 10, 3), D("120.00")), (date(2026, 10, 4), D("120.00"))]


# ---------- доступ: только постоплата ----------


@pytest.mark.parametrize("payment", ["upfront", "own_production"])
async def test_closed_for_other_supplier_settings(client, tokens, payment):
    await set_supplier(client, tokens, payment)
    r = await client.get(URL, headers=auth(tokens))
    assert r.status_code == 403 and "платит поставщику после продажи или с отсрочкой" in r.json()["detail"]
    r = await client.put(f"{URL}/setup", json={"start": "2026-10-01", "amount": "0"}, headers=auth(tokens))
    assert r.status_code == 403
    r = await client.get(f"{URL}/entries/template", headers=auth(tokens))
    assert r.status_code == 403


async def test_default_settings_have_no_payables(client, tokens):
    r = await client.get(URL, headers=auth(tokens))  # «Откуда товар» по умолчанию — закупаю и плачу сразу
    assert r.status_code == 403


# ---------- после продажи: долг по продажам ----------


async def after_sale_store(client, tokens, sessions):
    await set_supplier(client, tokens, "after_sale")
    uid = await user_id(client, tokens)
    await import_reference_costs(client, tokens)
    # по 2 шт. в день с 28.09 по 13.10 (16 дней): себестоимость с НДС 2 076,44 ₽ в день
    await add_sales(sessions, uid, days(date(2026, 9, 28), date(2026, 10, 13)), "5000", 2, OFFER)
    return uid


async def test_not_configured_until_start(client, tokens, sessions):
    await after_sale_store(client, tokens, sessions)
    body = await call(client, tokens, "GET")
    assert body["mode"] == "sales" and body["configured"] is False and body["summary"] is None
    assert body["schedule"]["title"] == "раз в неделю, в понедельник"
    # записи без начала учёта не принимаются
    r = await client.post(f"{URL}/entries", json={"kind": "payment", "day": "2026-10-10", "amount": "1"}, headers=auth(tokens))
    assert r.status_code == 400 and "Сначала укажите начало учёта" in r.json()["detail"]


async def test_sales_debt_with_opening_and_payments(client, tokens, sessions):
    await after_sale_store(client, tokens, sessions)
    body = await start(client, tokens, amount="10000")
    s = body["summary"]
    assert body["configured"] and body["start"] == "2026-09-28" and body["opening_amount"] == "10000.00"
    # 16 дней продаж по 2 076,44 + 10 000 на начало учёта
    assert s["accrued"] == "43223.04" and s["paid"] == "0.00" and s["debt"] == "43223.04" and s["prepaid"] == "0.00"
    # оплата за неделю — в понедельник следующей: 28.09–04.10 → 05.10 (просрочена на 9 дней), 05.10–11.10 → 12.10 (на 2),
    # 12.10–13.10 → 19.10 (ещё не пора); начальный долг — без срока
    assert s["overdue"] == "29070.16" and s["overdue_count"] == 2 and s["oldest_overdue_days"] == 9
    assert s["not_due"] == "4152.88"
    b = body["buckets"]
    assert b["no_term"] == "10000.00" and b["d1_30"] == "29070.16" and b["not_due"] == "4152.88"
    labels = [i["label"] for i in body["items"]]
    assert labels == ["Долг на начало учёта", "Продажи 28.09–04.10", "Продажи 05.10–11.10", "Продажи 12.10–18.10"]
    assert body["items"][3]["remaining"] == "4152.88" and body["items"][3]["period_to"] == "2026-10-18"
    assert s["due_7"] == "4152.88" and s["due_14"] == "4152.88"  # 19.10 — через 5 дней
    assert body["warnings"] == [] and body["incomplete"] is False
    assert body["series"][-1] == {"day": "2026-10-14", "debt": "43223.04"}

    # оплата 20 000: сначала закрывается начальный долг, потом самая ранняя неделя
    row = await call(client, tokens, "POST", "/entries", 201, json={
        "kind": "payment", "day": "2026-10-10", "amount": "20000", "counterparty": "ТЕХИНТЕЛС"})
    assert row["source"] == "manual" and row["amount"] == "20000.00"
    body = await call(client, tokens, "GET")
    s = body["summary"]
    assert s["paid"] == "20000.00" and s["debt"] == "23223.04" and s["overdue"] == "19070.16" and s["paid_30d"] == "20000.00"
    assert body["items"][0]["remaining"] == "4535.08"
    assert [e["kind"] for e in body["entries"]] == ["payment"]


async def test_gaps_and_missing_costs_are_flagged_not_zero(client, tokens, sessions):
    uid = await after_sale_store(client, tokens, sessions)
    async with sessions() as db:  # товар, которого нет в справочнике себестоимости, продан 13.10 — 3 шт.
        product = Product(id=uuid.uuid4(), user_id=uid, ozon_product_id=2, ozon_sku=1002, offer_id="NO.COST.ITEM",
                          name="Без себестоимости", synced_at=datetime.now(timezone.utc))
        db.add(product)
        revenue = (await db.execute(select(AccrualType).where(AccrualType.name_ru == "Выручка"))).scalar_one()
        await db.flush()
        db.add(Accrual(user_id=uid, product_id=product.id, accrual_type_id=revenue.id, accrual_date=date(2026, 10, 13),
                       amount=D("1000"), quantity=3, source=SOURCE_API, sync_scope="day:2026-10-13", line_no=1))
        await db.commit()
    body = await start(client, tokens, day="2026-09-20", amount="0")  # 20–27.09 — данных нет
    assert body["incomplete"] is True
    text = " ".join(body["warnings"])
    assert "Нет данных о продажах за 8 дн." in text and "20.09.2026 по 27.09.2026" in text
    assert "нет себестоимости: 3 шт." in text
    assert body["summary"]["debt"] == "33223.04"  # дни без данных не считаем нулевыми и не придумываем


async def test_operation_payments_count_automatically(client, tokens, sessions):
    uid = await after_sale_store(client, tokens, sessions)
    cats, accounts = await setup(client, tokens, supplier="after_sale")
    await start(client, tokens, amount="0")
    base = "/api/v1/planning/operations"
    for amount, day in (("-5000", "2026-10-05"), ("1000", "2026-10-06")):  # оплата и возврат от поставщика
        r = await client.post(base, headers=auth(tokens), json={
            "type": "expense" if amount.startswith("-") else "income", "amount": amount.lstrip("-"), "day": day,
            "account_id": accounts["Расчётный счёт"], "category_id": cats["supplier"], "counterparty": "ТЕХИНТЕЛС"})
        assert r.status_code in (200, 201), r.text
    # операция другой статьи — не оплата поставщику
    r = await client.post(base, headers=auth(tokens), json={
        "type": "expense", "amount": "777", "day": "2026-10-07", "account_id": accounts["Расчётный счёт"],
        "category_id": cats["rent"]})
    assert r.status_code in (200, 201), r.text
    body = await call(client, tokens, "GET")
    assert body["operations_counted"] is True
    assert body["summary"]["paid"] == "4000.00"  # 5 000 оплачено − 1 000 возврат
    ops = [e for e in body["entries"] if e["source"] == "operation"]
    assert len(ops) == 2 and all(not e["editable"] for e in ops)
    assert sorted(e["amount"] for e in ops) == ["-1000.00", "5000.00"]


# ---------- с отсрочкой: закупки ----------


async def test_deferred_purchases_due_dates_versions_and_delete(client, tokens):
    await set_supplier(client, tokens, "deferred", 30)
    body = await start(client, tokens, day="2026-09-01", amount="0")
    assert body["mode"] == "purchases" and body["schedule"] is None and body["deferral_days"] == 30
    assert body["summary"]["debt"] == "0.00" and body["items"] == []

    first = await call(client, tokens, "POST", "/entries", 201, json={
        "kind": "purchase", "day": "2026-09-05", "amount": "100000", "number": "УПД-15", "counterparty": "ТЕХИНТЕЛС"})
    assert first["due_day"] == "2026-10-05"  # дата закупки + отсрочка из настроек
    await call(client, tokens, "POST", "/entries", 201, json={
        "kind": "purchase", "day": "2026-10-10", "amount": "50000", "due_day": "2026-10-25"})
    body = await call(client, tokens, "GET")
    s = body["summary"]
    assert s["debt"] == "150000.00" and s["overdue"] == "100000.00" and s["oldest_overdue_days"] == 9
    assert s["due_14"] == "50000.00" and body["buckets"]["d1_30"] == "100000.00"
    assert body["items"][0]["label"] == "Закупка № УПД-15"

    # исправление — новая версия, прежняя остаётся в истории
    changed = await call(client, tokens, "PATCH", f"/entries/{first['entry_id']}", json={"amount": "90000"})
    assert changed["version"] == 2 and changed["amount"] == "90000.00"
    assert (await call(client, tokens, "GET"))["summary"]["debt"] == "140000.00"
    # оплата закрывает долг со старым сроком первым
    await call(client, tokens, "POST", "/entries", 201, json={"kind": "payment", "day": "2026-10-12", "amount": "90000"})
    s = (await call(client, tokens, "GET"))["summary"]
    assert s["overdue"] == "0.00" and s["debt"] == "50000.00" and s["not_due"] == "50000.00"
    # удаление — запись уходит из расчёта
    other = [e for e in (await call(client, tokens, "GET"))["entries"] if e["kind"] == "purchase" and e["amount"] == "50000.00"][0]
    await call(client, tokens, "DELETE", f"/entries/{other['entry_id']}", 204)
    assert (await call(client, tokens, "GET"))["summary"]["debt"] == "0.00"
    await call(client, tokens, "PATCH", f"/entries/{other['entry_id']}", 404, json={"amount": "1"})


async def test_entry_validation(client, tokens):
    await set_supplier(client, tokens, "deferred", 14)
    await start(client, tokens, day="2026-10-01", amount="500")
    bad = [
        ({"kind": "purchase", "day": "2026-09-30", "amount": "1"}, "раньше начала учёта"),
        ({"kind": "purchase", "day": "2026-10-20", "amount": "1"}, "не позже сегодняшнего дня"),
        ({"kind": "purchase", "day": "2026-10-05", "amount": "0"}, "Сумма — больше нуля"),
        ({"kind": "purchase", "day": "2026-10-05", "amount": "5", "due_day": "2026-10-01"}, "Срок оплаты раньше даты закупки"),
        ({"kind": "payment", "day": "2026-10-05", "amount": "-5"}, "Сумма — больше нуля"),
    ]
    for body, text in bad:
        r = await client.post(f"{URL}/entries", json=body, headers=auth(tokens))
        assert r.status_code == 400 and text in r.json()["detail"], (body, r.text)
    # начальный долг меняется только через «Начало учёта»: версия, не перезапись
    body = await start(client, tokens, day="2026-10-01", amount="800")
    assert body["opening_amount"] == "800.00" and body["summary"]["debt"] == "800.00"
    r = await client.put(f"{URL}/setup", json={"start": "2026-10-20", "amount": "0"}, headers=auth(tokens))
    assert r.status_code == 400 and "не позже сегодняшнего" in r.json()["detail"]
    r = await client.put(f"{URL}/setup", json={"start": "2026-10-01", "amount": "-1"}, headers=auth(tokens))
    assert r.status_code == 400


async def test_purchases_not_allowed_when_paying_after_sale(client, tokens):
    await set_supplier(client, tokens, "after_sale")
    await start(client, tokens, day="2026-10-01", amount="0")
    r = await client.post(f"{URL}/entries", json={"kind": "purchase", "day": "2026-10-05", "amount": "10"}, headers=auth(tokens))
    assert r.status_code == 400 and "долг считается по продажам сам" in r.json()["detail"]


async def test_deferred_without_default_days_needs_due_date(client, tokens):
    await set_supplier(client, tokens, "mixed")
    await start(client, tokens, day="2026-10-01", amount="0")
    r = await client.post(f"{URL}/entries", json={"kind": "purchase", "day": "2026-10-05", "amount": "10"}, headers=auth(tokens))
    assert r.status_code == 400 and "Укажите срок оплаты" in r.json()["detail"]
    row = await call(client, tokens, "POST", "/entries", 201, json={
        "kind": "purchase", "day": "2026-10-05", "amount": "10", "due_day": "2026-10-15"})
    assert row["due_day"] == "2026-10-15"
    # «по-разному»: долг по продажам сам не начисляется (не знаем, что оплачено при закупке) — только записи
    assert (await call(client, tokens, "GET"))["mode"] == "purchases"


async def test_other_user_entries_are_not_visible(client, tokens):
    await set_supplier(client, tokens, "deferred", 7)
    await start(client, tokens, day="2026-10-01", amount="0")
    row = await call(client, tokens, "POST", "/entries", 201, json={"kind": "purchase", "day": "2026-10-05", "amount": "10"})
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1", "full_name": "Другой"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    await set_supplier(client, other, "deferred", 7)
    r = await client.patch(f"{URL}/entries/{row['entry_id']}", json={"amount": "1"}, headers=auth(other))
    assert r.status_code == 404
    assert (await call(client, other, "GET"))["configured"] is False


# ---------- шаблон и загрузка списком ----------


def xlsx(rows: list[list], header=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header or ["ID (не меняйте)", "Вид", "Дата", "Сумма с НДС, руб.", "Срок оплаты", "№ документа", "Контрагент", "Описание"])
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def upload(client, tokens, content, name="f.xlsx", dry_run=True, expect=200):
    r = await client.post(f"{URL}/entries/import", params={"dry_run": dry_run}, headers=auth(tokens),
                          files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == expect, r.text
    return r.json()


async def test_template_import_roundtrip(client, tokens):
    await set_supplier(client, tokens, "deferred", 30)
    await start(client, tokens, day="2026-09-01", amount="0")
    r = await client.get(f"{URL}/entries/template", params={"rows": "empty"}, headers=auth(tokens))
    assert r.status_code == 200 and r.content[:2] == b"PK"  # xlsx
    content = xlsx([
        [None, "закупка", date(2026, 9, 5), 100000, None, "УПД-1", "ТЕХИНТЕЛС", None],
        [None, "оплата", date(2026, 10, 1), 40000, None, None, "ТЕХИНТЕЛС", "частично"],
        [None, "оплата", date(2026, 10, 2), None, None, None, None, None],  # без суммы — пропуск
        [None, "закупка", date(2026, 8, 1), 5, None, None, None, None],     # раньше начала учёта
        [None, "возврат", date(2026, 10, 2), 5, None, None, None, None],     # неизвестный вид
    ])
    preview = await upload(client, tokens, content)
    assert preview["applied"] is False and preview["counts"] == {"new": 2, "changed": 0, "unchanged": 0, "skipped": 1, "error": 2}
    assert "раньше начала учёта" in preview["rows"][3]["message"] and "вид" in preview["rows"][4]["message"]
    assert (await call(client, tokens, "GET"))["summary"]["debt"] == "0.00"  # предпросмотр ничего не сохранил
    applied = await upload(client, tokens, content, dry_run=False)
    assert applied["applied"] is True and applied["counts"]["new"] == 2
    s = (await call(client, tokens, "GET"))["summary"]
    assert s["accrued"] == "100000.00" and s["paid"] == "40000.00" and s["debt"] == "60000.00"
    # тот же файл второй раз — не задваивает
    again = await upload(client, tokens, content, dry_run=False)
    assert again["counts"]["new"] == 0 and again["counts"]["skipped"] == 2 + 1
    assert (await call(client, tokens, "GET"))["summary"]["debt"] == "60000.00"

    # скачанный из сервиса файл загружается обратно без ошибок и без изменений
    filled = await client.get(f"{URL}/entries/template", headers=auth(tokens))
    back = await upload(client, tokens, filled.content)
    assert back["counts"] == {"new": 0, "changed": 0, "unchanged": 2, "skipped": 0, "error": 0}
    # исправили сумму в строке с ID — новая версия
    wb = openpyxl.load_workbook(io.BytesIO(filled.content))
    ws = wb.worksheets[0]
    header = [c.value for c in ws[1]]
    col = next(i for i, h in enumerate(header, 1) if h and h.startswith("Сумма"))
    kind_col = header.index("Вид") + 1
    for row in ws.iter_rows(min_row=2):
        if row[kind_col - 1].value == "закупка":
            row[col - 1].value = 90000
    out = io.BytesIO()
    wb.save(out)
    changed = await upload(client, tokens, out.getvalue(), dry_run=False)
    assert changed["counts"]["changed"] == 1
    assert (await call(client, tokens, "GET"))["summary"]["debt"] == "50000.00"

    # файл с ошибками — теми же колонками плюс «Ошибка»
    r = await client.post(f"{URL}/entries/import/errors", headers=auth(tokens),
                          files={"file": ("f.xlsx", content, "application/octet-stream")})
    assert r.status_code == 200
    errors = openpyxl.load_workbook(io.BytesIO(r.content)).worksheets[0]
    assert errors.max_row == 3 and errors.cell(1, 1).value == "Ошибка"


async def test_import_csv_and_wrong_file(client, tokens):
    await set_supplier(client, tokens, "deferred", 10)
    await start(client, tokens, day="2026-09-01", amount="0")
    csv = "Вид;Дата;Сумма\nзакупка;05.10.2026;1000,50\n".encode("utf-8")
    body = await upload(client, tokens, csv, name="x.csv", dry_run=False)
    assert body["counts"]["new"] == 1
    assert (await call(client, tokens, "GET"))["summary"]["debt"] == "1000.50"
    r = await client.post(f"{URL}/entries/import", headers=auth(tokens),
                          files={"file": ("x.csv", "один;два\n1;2\n".encode(), "text/csv")})
    assert r.status_code == 400 and "Не найдены колонки" in r.json()["detail"]
