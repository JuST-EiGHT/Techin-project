"""Долги поставщикам (шаг 10.23a): поставки от поставщика, продажи по ним FIFO внутри артикула, возвраты по
отправлению, оплаты общим потоком, закрытие с остатком; разбор документа поставщика (PDF) и шаблона; доступ — только
при оплате после продажи."""
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import select

from app.api.v1 import payables as payables_api
from app.api.v1 import planning as planning_api
from app.engine import supplier_lots as eng
from app.integrations import supplier_docs
from app.models.accrual import SOURCE_API, Accrual, AccrualDay, AccrualType
from app.models.product import Product
from app.models.supplier_lot import SupplierLot
from app.services import planning, supplier_lots, supplier_lots_upload, tax_profile
from tests.test_pnl_api import user_id
from tests.test_report_pnl import auth, import_reference_costs

D = Decimal
TODAY = date(2026, 11, 20)
URL = "/api/v1/finance/payables"
PDF = Path(__file__).parent / "fixtures" / "supplier_pick_list.pdf"
TH6202 = "6202-2RS.C3.SRL.NAC.TH"  # себестоимость 165 ₽, НДС 22 % → 201,30 ₽ за штуку
TH6303 = "6303-2RS.C3.SRL.NAC.TH"  # 269 ₽ → 328,18 ₽
JP6205 = "6205-2RS.C3.SRL.NAC.JP"  # 322,22 ₽ → 393,1084 ₽
JP6204 = "6204-2Z.C3.SRL.NAC.JP"  # в справочнике себестоимости нет (есть только 6204-2Z.C3.SRL.NAC.TH)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for module in (supplier_lots, supplier_lots_upload, payables_api, planning, planning_api, tax_profile):
        monkeypatch.setattr(module, "today_msk", lambda: TODAY)


# ---------- расчётный модуль ----------


def line(key, qty, price, **kw):
    return eng.LineIn(key, key, qty, price=None if price is None else D(price), **kw)


def lot(lot_id, day, *lines, closed_on=None):
    return eng.LotIn(lot_id, day, lot_id, tuple(lines), closed_on=closed_on)


def sale(day, key, qty, posting=None, seq=0):
    return eng.Event(day, key, qty, "sale", posting, seq)


def ret(day, key, qty, posting=None, seq=0):
    return eng.Event(day, key, qty, "return", posting, seq)


def by_id(result):
    return {o.lot.lot_id: o for o in result.lots}


def test_fifo_inside_article_example_of_owner():
    """Пример владельца: поставка 1 — А, Б, В; поставка 2 — снова А. Продажи А идут в поставку 1, пока там А не
    кончится, потом — в поставку 2; поставка 1 остаётся открытой из-за Б и В."""
    lots = [
        lot("1", date(2026, 10, 1), line("a", 10, "1000"), line("b", 5, "2000"), line("c", 4, "500")),
        lot("2", date(2026, 10, 20), line("a", 10, "1100")),
    ]
    events = [sale(date(2026, 10, 5), "a", 7, "P1"), sale(date(2026, 10, 22), "a", 5, "P2")]
    res = eng.allocate(lots, events, [])
    one, two = by_id(res)["1"], by_id(res)["2"]
    assert [(ln.line.key, ln.sold, ln.remaining) for ln in one.lines] == [("a", 10, 0), ("b", 0, 5), ("c", 0, 4)]
    assert two.lines[0].sold == 2 and two.lines[0].remaining == 8
    assert one.debt == D("10000.00") and two.debt == D("2200.00")  # разные цены партий
    assert one.status == two.status == eng.SELLING and res.to_pay == D("12200.00")
    # продажа раньше даты поставки 2 в неё не идёт: А в поставке 1 кончилось — «продано без поставки»
    res = eng.allocate(lots, [sale(date(2026, 10, 5), "a", 12)], [])
    assert by_id(res)["1"].lines[0].sold == 10 and by_id(res)["2"].lines[0].sold == 0 and res.unallocated == {"a": 2}


def test_return_goes_back_to_lot_of_its_posting():
    lots = [lot("1", date(2026, 10, 1), line("a", 5, "100")), lot("2", date(2026, 10, 10), line("a", 5, "120"))]
    events = [
        sale(date(2026, 10, 2), "a", 4, "P1"),   # поставка 1: 4 из 5
        sale(date(2026, 10, 12), "a", 3, "P2"),  # 1 — в поставку 1, 2 — в поставку 2
        ret(date(2026, 10, 15), "a", 1, "P1"),   # вернули штуку из P1 — она из поставки 1, хотя последней продавали из 2
    ]
    res = eng.allocate(lots, events, [])
    one, two = by_id(res)["1"], by_id(res)["2"]
    # было к оплате за 5 шт. в поставке 1 — стало за 4; штука снова в остатке поставки 1
    assert (one.lines[0].sold, one.lines[0].returned, one.lines[0].remaining) == (4, 1, 1)
    assert two.lines[0].sold == 2 and one.debt == D("400.00") and two.debt == D("240.00")
    assert [(m.qty, m.posting) for m in one.moves] == [(4, "P1"), (1, "P2"), (-1, "P1")]
    # следующая продажа снова берёт самую старую поставку с остатком — первую
    res = eng.allocate(lots, events + [sale(date(2026, 10, 16), "a", 1, "P3")], [])
    assert by_id(res)["1"].lines[0].remaining == 0 and by_id(res)["2"].lines[0].sold == 2


def test_return_without_posting_reverses_last_sale_and_orphans():
    lots = [lot("1", date(2026, 10, 1), line("a", 5, "100")), lot("2", date(2026, 10, 10), line("a", 5, "120"))]
    events = [sale(date(2026, 10, 2), "a", 5), sale(date(2026, 10, 12), "a", 2), ret(date(2026, 10, 13), "a", 1),
              ret(date(2026, 10, 14), "b", 2)]
    res = eng.allocate(lots, events, [])
    assert by_id(res)["2"].lines[0].sold == 1 and by_id(res)["1"].lines[0].sold == 5  # последняя продажа — из поставки 2
    assert res.orphan_returns == {"b": 2}
    # штуки отправления, проданные без поставки, возвращаются первыми — долг поставок не трогают
    res = eng.allocate([lot("1", date(2026, 10, 1), line("a", 1, "100"))],
                       [sale(date(2026, 10, 2), "a", 3, "P"), ret(date(2026, 10, 3), "a", 2, "P")], [])
    assert res.lots[0].lines[0].sold == 1 and res.unallocated == {}


def test_payments_fifo_prepaid_and_statuses():
    lots = [
        lot("1", date(2026, 10, 1), line("a", 2, "1000")),
        lot("2", date(2026, 10, 10), line("a", 3, "1000"), line("b", 1, None)),
        lot("3", date(2026, 10, 11), line("c", 2, "50", written_off=1, sold_before=1)),  # 1 списана, 1 продана раньше
    ]
    events = [sale(date(2026, 10, 12), "a", 4), sale(date(2026, 10, 12), "b", 1)]
    res = eng.allocate(lots, events, [eng.Payment(date(2026, 10, 15), D("2500"))])
    one, two, three = by_id(res)["1"], by_id(res)["2"], by_id(res)["3"]
    assert (one.paid, one.unpaid, one.status) == (D("2000.00"), D("0.00"), eng.DONE)  # всё продано и оплачено
    assert (two.debt, two.paid, two.unpaid, two.unknown_price) == (D("2000.00"), D("500.00"), D("1500.00"), 1)
    assert two.status == eng.SELLING and three.remaining == 0 and three.debt == D("50.00")
    assert three.status == eng.AWAITING and res.to_pay == D("1550.00")
    # заплатили больше — аванс, а не отрицательный долг
    res = eng.allocate(lots, events, [eng.Payment(date(2026, 10, 15), D("5000"))])
    assert res.to_pay == 0 and res.prepaid == D("950.00")  # 5 000 − (2 000 + 2 000 + 50)
    # закрытая вручную поставка не берёт продажи после дня закрытия, а проданное до него — остаётся в ней
    closed = [lot("1", date(2026, 10, 1), line("a", 5, "10"), closed_on=date(2026, 10, 3))]
    res = eng.allocate(closed, [sale(date(2026, 10, 2), "a", 1), sale(date(2026, 10, 4), "a", 1)], [])
    assert res.lots[0].status == eng.CLOSED and res.lots[0].lines[0].sold == 1 and res.unallocated == {"a": 1}


# ---------- документ поставщика ----------


def test_parse_pick_list_pdf():
    doc = supplier_docs.parse_pdf(PDF.read_bytes())
    assert (doc.number, doc.day, doc.warehouse, doc.total_qty) == ("587", date(2026, 11, 10), "Основной склад", 31)
    assert doc.supplier == 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ТЕХИНТЕЛС"'
    assert [(ln.article, ln.qty, ln.barcode) for ln in doc.lines] == [
        (JP6204, 2, "2000000043630"), ("6007-2Z.CM.SRL.NAC.JP", 4, "2000000037356"), (JP6205, 3, "2000000043425"),
        (TH6202, 17, None), (TH6303, 5, None)]
    assert doc.errors == [] and doc.warnings == [] and doc.lines_qty == 31


def test_parse_qty_and_broken_files():
    assert supplier_docs.parse_qty("17") == 17 and supplier_docs.parse_qty("17,000") == 17
    assert supplier_docs.parse_qty("5 шт") == 5 and supplier_docs.parse_qty("0", allow_zero=True) == 0
    for bad in ("", "1,5", "-2", "0", "два"):
        with pytest.raises(ValueError):
            supplier_docs.parse_qty(bad)
    with pytest.raises(supplier_docs.DocError, match="Не удалось открыть PDF"):
        supplier_docs.parse_pdf(b"%PDF-1.4 broken")
    # таблица без «Артикула» — понятная ошибка, а не пустая поставка
    with pytest.raises(supplier_docs.DocError, match="не нашли таблицу"):
        supplier_docs._rows(supplier_docs.Doc(None, None), [[["Ячейка", "Наименование", "Количество"], ["A1", "X", "2"]]])
    doc = supplier_docs.Doc(None, None)
    supplier_docs._rows(doc, [[["Артикул", "Кол-во", "Ед. изм."], ["X-1", "3", "упак"], ["", "2", "шт"], ["Итого", "5", ""]]])
    assert [ln.error for ln in doc.lines] == ["единица «упак» — сервис считает поставку в штуках",
                                             "нет артикула — строку не к чему отнести"]
    assert supplier_docs.check(doc).errors[:2] == [
        "Не нашли номер документа («… № 587 от 10.11.2026») — укажите его в шаблоне",
        "Не нашли дату документа («… № 587 от 10.11.2026») — укажите её в шаблоне"]


# ---------- через API ----------


async def set_supplier(client, tokens, payment="after_sale", days_=None):
    body = {"supplier_payment": payment, **({"supplier_deferral_days": days_} if days_ else {})}
    r = await client.patch("/api/v1/settings", json=body, headers=auth(tokens))
    assert r.status_code == 200, r.text


async def call(client, tokens, method, path="", expect=200, **kw):
    r = await client.request(method, f"{URL}{path}", headers=auth(tokens), **kw)
    assert r.status_code == expect, r.text
    return r.json() if r.content else None


async def upload(client, tokens, content, name="pick_list-587.pdf", dry_run=True, sold_before=None, expect=200):
    data = {"sold_before": json.dumps(sold_before)} if sold_before else None
    r = await client.post(f"{URL}/lots/upload", params={"dry_run": dry_run}, headers=auth(tokens), data=data,
                          files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == expect, r.text
    return r.json()


async def add_sales(sessions, uid, rows: list[tuple]):
    """Начисления из API: (день, артикул, штук, отправление, «sale» | «return»); дни отмечены загруженными."""
    async with sessions() as db:
        types = {t.name_ru: t.id for t in (await db.execute(select(AccrualType))).scalars()}
        for i, name in ((901, "Выручка"), (902, "Возврат выручки")):
            if name not in types:
                db.add(AccrualType(id=i, name_ru=name))
                types[name] = i
        products = {p.offer_id: p.id for p in (await db.execute(select(Product).where(Product.user_id == uid))).scalars()}
        await db.flush()
        days = {d for (d, *_rest) in rows}
        lines = {d: (await db.execute(select(Accrual).where(Accrual.user_id == uid, Accrual.accrual_date == d))).scalars().all()
                 for d in days}
        for n, (d, offer, qty, posting, kind) in enumerate(rows):
            if offer not in products:
                product = Product(id=uuid.uuid4(), user_id=uid, ozon_product_id=1000 + len(products), offer_id=offer,
                                  name="Подшипник", synced_at=datetime.now(timezone.utc))
                db.add(product)
                products[offer] = product.id
                await db.flush()
            amount = D("500") * qty * (1 if kind == "sale" else -1)
            db.add(Accrual(user_id=uid, product_id=products[offer], posting_number=posting,
                           accrual_type_id=types["Выручка" if kind == "sale" else "Возврат выручки"], accrual_date=d,
                           amount=amount, quantity=qty, source=SOURCE_API, sync_scope=f"day:{d}",
                           line_no=len(lines[d]) + n))
        for d in days:
            if await db.get(AccrualDay, (uid, d)) is None:
                db.add(AccrualDay(user_id=uid, day=d, synced_at=datetime.now(timezone.utc), entries=1))
        await db.commit()


async def mark_days(sessions, uid, start: date, end: date):
    """Дни с загруженными начислениями (без продаж): не пропуск, а «продаж не было»."""
    async with sessions() as db:
        d = start
        while d <= end:
            if await db.get(AccrualDay, (uid, d)) is None:
                db.add(AccrualDay(user_id=uid, day=d, synced_at=datetime.now(timezone.utc), entries=0))
            d += timedelta(days=1)
        await db.commit()


def template_file(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["№ поставки", "Дата поставки", "Поставщик", "Артикул", "Наименование", "Количество, шт.",
               "Уже продано ранее, шт."])
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.mark.parametrize("payment", ["upfront", "deferred", "mixed", "own_production"])
async def test_only_for_payment_after_sale(client, tokens, payment):
    await set_supplier(client, tokens, payment, 30 if payment == "deferred" else None)
    r = await client.get(URL, headers=auth(tokens))
    assert r.status_code == 403 and "после продажи" in r.json()["detail"]
    r = await client.post(f"{URL}/lots/upload", headers=auth(tokens), files={"file": ("a.pdf", PDF.read_bytes(), "x")})
    assert r.status_code == 403
    r = await client.post(f"{URL}/payments", json={"day": "2026-11-01", "amount": "1"}, headers=auth(tokens))
    assert r.status_code == 403


async def test_upload_pdf_preview_apply_and_reupload(client, tokens, sessions):
    await set_supplier(client, tokens)
    await import_reference_costs(client, tokens)
    empty = await call(client, tokens, "GET")
    assert empty["configured"] is False and empty["lots"] == [] and empty["summary"]["to_pay"] == "0.00"

    preview = await upload(client, tokens, PDF.read_bytes())
    assert preview["applied"] is False and preview["format"] == "pdf" and preview["counts"]["new"] == 1
    doc = preview["docs"][0]
    assert (doc["number"], doc["day"], doc["status"], doc["total_qty"], doc["lines_qty"]) == ("587", "2026-11-10", "new", 31, 31)
    prices = {ln["article"]: ln["price"] for ln in doc["lines"]}
    assert prices == {JP6204: None, "6007-2Z.CM.SRL.NAC.JP": None, JP6205: "393.11", TH6202: "201.30", TH6303: "328.18"}
    assert doc["no_price"] == 2 and doc["amount"] == "6242.33"  # 17 × 201,30 + 3 × 393,11 + 5 × 328,18
    assert (await call(client, tokens, "GET"))["configured"] is False  # предпросмотр ничего не сохранил

    applied = await upload(client, tokens, PDF.read_bytes(), dry_run=False)
    assert applied["applied"] is True and applied["counts"]["new"] == 1
    body = await call(client, tokens, "GET")
    assert body["configured"] and len(body["lots"]) == 1
    lot_ = body["lots"][0]
    assert (lot_["number"], lot_["status"], lot_["qty"], lot_["sold"], lot_["remaining"]) == ("587", "selling", 31, 0, 31)
    assert lot_["has_document"] and body["summary"]["stock_qty"] == 31 and body["summary"]["stock_value"] == "6242.33"
    # документ хранится как есть
    r = await client.get(f"{URL}/lots/{lot_['lot_id']}/document", headers=auth(tokens))
    assert r.status_code == 200 and r.content == PDF.read_bytes()
    # тот же документ ещё раз — не дубль
    again = await upload(client, tokens, PDF.read_bytes(), dry_run=False)
    assert again["counts"] == {"new": 0, "changed": 0, "unchanged": 1, "error": 0} and again["applied"] is False
    assert len((await call(client, tokens, "GET"))["lots"]) == 1


async def test_sales_returns_and_payments_through_api(client, tokens, sessions):
    await set_supplier(client, tokens)
    uid = await user_id(client, tokens)
    await import_reference_costs(client, tokens)
    await upload(client, tokens, PDF.read_bytes(), dry_run=False)  # № 587 от 10.11: 6202 TH × 17
    # вторая поставка — шаблоном: № 600 от 12.11, 6202 TH × 10
    second = await upload(client, tokens, template_file([["600", date(2026, 11, 12), "ТЕХИНТЕЛС", TH6202, None, 10, None]]),
                          name="lots.xlsx", dry_run=False)
    assert second["counts"]["new"] == 1 and second["format"] == "xlsx"
    await mark_days(sessions, uid, date(2026, 11, 10), TODAY)
    await add_sales(sessions, uid, [
        (date(2026, 11, 11), TH6202, 15, "P-1", "sale"),   # № 587: 15 из 17
        (date(2026, 11, 13), TH6202, 4, "P-2", "sale"),    # 2 — в № 587 (кончилось), 2 — в № 600
        (date(2026, 11, 15), TH6202, 1, "P-1", "return"),  # возврат из P-1 → снова в № 587
        (date(2026, 11, 16), TH6303, 2, "P-3", "sale"),
    ])
    body = await call(client, tokens, "GET")
    lots = {lt["number"]: lt for lt in body["lots"]}
    assert [lt["number"] for lt in body["lots"]] == ["600", "587"]  # новые сверху
    l587 = {ln["article"]: ln for ln in lots["587"]["lines"]}
    assert (l587[TH6202]["sold"], l587[TH6202]["returned"], l587[TH6202]["remaining"]) == (16, 1, 1)
    assert l587[TH6303]["sold"] == 2 and lots["600"]["lines"][0]["sold"] == 2
    # 16 × 201,30 + 2 × 328,18 = 3 877,16; № 600 — 2 × 201,30
    assert lots["587"]["debt"] == "3877.16" and lots["600"]["debt"] == "402.60"
    assert body["summary"]["to_pay"] == "4279.76" and body["warnings"] == []
    moves = lots["587"]["moves"]
    assert {"day": "2026-11-15", "article": TH6202, "kind": "return", "qty": 1} in moves

    # оплата 4 000: сначала закрывается старшая поставка
    pay = await call(client, tokens, "POST", "/payments", 201, json={"day": "2026-11-18", "amount": "4000",
                                                                    "counterparty": "ТЕХИНТЕЛС"})
    body = await call(client, tokens, "GET")
    lots = {lt["number"]: lt for lt in body["lots"]}
    assert (lots["587"]["paid"], lots["587"]["unpaid"]) == ("3877.16", "0.00")
    assert (lots["600"]["paid"], lots["600"]["unpaid"]) == ("122.84", "279.76")
    assert body["summary"]["paid_30d"] == "4000.00" and body["payments"][0]["source"] == "manual"
    # исправили оплату — новая версия
    fixed = await call(client, tokens, "PATCH", f"/payments/{pay['payment_id']}", json={"amount": "3877.16"})
    assert fixed["version"] == 2
    assert {lt["number"]: lt for lt in (await call(client, tokens, "GET"))["lots"]}["600"]["paid"] == "0.00"
    await call(client, tokens, "DELETE", f"/payments/{pay['payment_id']}", 204)
    assert (await call(client, tokens, "GET"))["summary"]["paid_total"] == "0.00"


async def test_operations_payments_count(client, tokens, sessions):
    await set_supplier(client, tokens)
    await import_reference_costs(client, tokens)
    await client.post("/api/v1/planning/setup", headers=auth(tokens), json={
        "supplier_payment": "after_sale", "accounts": [{"name": "Счёт", "kind": "checking", "balance": "0"}],
        "ozon_account": 0, "categories": ["supplier", "rent"]})
    cats = {c["code"]: c["id"] for c in (await client.get("/api/v1/planning/categories", headers=auth(tokens))).json()}
    account = (await client.get("/api/v1/planning/accounts", headers=auth(tokens))).json()[0]["id"]
    await upload(client, tokens, PDF.read_bytes(), dry_run=False)
    for kind, amount, cat in (("expense", "1000", "supplier"), ("income", "200", "supplier"), ("expense", "50", "rent")):
        r = await client.post("/api/v1/planning/operations", headers=auth(tokens), json={
            "type": kind, "amount": amount, "day": "2026-11-12", "account_id": account, "category_id": cats[cat]})
        assert r.status_code in (200, 201), r.text
    body = await call(client, tokens, "GET")
    assert body["operations_counted"] is True and body["summary"]["paid_total"] == "800.00"  # аренда не в счёт
    assert body["summary"]["prepaid"] == "800.00"  # ничего ещё не продано — аванс
    assert {p["source"] for p in body["payments"]} == {"operation"} and not body["payments"][0]["editable"]


async def test_close_reopen_sold_before_and_warnings(client, tokens, sessions):
    await set_supplier(client, tokens)
    uid = await user_id(client, tokens)
    await import_reference_costs(client, tokens)
    await mark_days(sessions, uid, date(2026, 11, 14), TODAY)  # 10–13.11 — данных нет
    await upload(client, tokens, PDF.read_bytes(), dry_run=False)
    body = await call(client, tokens, "GET")
    assert body["sales_known_from"] == "2026-11-14"
    assert any("Нет данных о продажах за 4 дн." in w for w in body["warnings"])
    lot_id = body["lots"][0]["lot_id"]

    # старая поставка: 5 шт. 6202 проданы до того, как сервис видит продажи, — в долг, в остаток не идут
    body = await call(client, tokens, "PATCH", f"/lots/{lot_id}", json={"sold_before": {TH6202: 5}, "comment": "старая"})
    lt = body["lots"][0]
    l6202 = next(ln for ln in lt["lines"] if ln["article"] == TH6202)
    assert (l6202["sold_before"], l6202["remaining"], l6202["amount"]) == (5, 12, "1006.50") and lt["version"] == 2
    assert lt["comment"] == "старая"
    r = await client.patch(f"{URL}/lots/{lot_id}", json={"sold_before": {TH6202: 18}}, headers=auth(tokens))
    assert r.status_code == 400 and "от 0 до 17" in r.json()["detail"]
    # продали без себестоимости — долг неизвестен, предупреждение
    await add_sales(sessions, uid, [(date(2026, 11, 15), JP6204, 1, "P-9", "sale")])
    body = await call(client, tokens, "GET")
    assert body["summary"]["unknown_price_qty"] == 1 and any("без себестоимости" in w for w in body["warnings"])

    # закрыть с остатком: «списано» = остаток, статус «закрыта», новых продаж не берёт
    body = await call(client, tokens, "POST", f"/lots/{lot_id}/close", json={"reason": "returned", "comment": "вернули"})
    lt = body["lots"][0]
    assert lt["status"] == "closed" and lt["remaining"] == 0 and lt["close_reason"] == "returned"
    assert next(ln for ln in lt["lines"] if ln["article"] == TH6202)["written_off"] == 12
    await add_sales(sessions, uid, [(date(2026, 11, 16), TH6202, 2, "P-10", "sale")])
    body = await call(client, tokens, "GET")
    assert body["summary"]["unallocated_qty"] == 2 and body["unallocated"] == [{"article": TH6202, "qty": 2}]
    body = await call(client, tokens, "POST", f"/lots/{lot_id}/reopen")
    assert body["lots"][0]["status"] == "selling" and body["summary"]["unallocated_qty"] == 0
    async with sessions() as db:  # все версии на месте (правило 12)
        versions = (await db.execute(select(SupplierLot).where(SupplierLot.lot_id == uuid.UUID(lot_id)))).scalars().all()
        assert sorted(v.version for v in versions) == [1, 2, 3, 4]
    await call(client, tokens, "DELETE", f"/lots/{lot_id}", 204)
    assert (await call(client, tokens, "GET"))["lots"] == []


async def test_template_roundtrip_and_errors(client, tokens, sessions):
    await set_supplier(client, tokens)
    await import_reference_costs(client, tokens)
    content = template_file([
        ["600", date(2026, 11, 12), "ТЕХИНТЕЛС", TH6202, "Подшипник", 10, 3],
        ["600", date(2026, 11, 12), "ТЕХИНТЕЛС", TH6303, None, 4, None],
        ["601", date(2026, 11, 13), None, TH6202, None, "1,5", None],     # дробное количество
        [None, date(2026, 11, 13), None, TH6202, None, 2, None],          # нет №
        ["602", date(2026, 12, 1), None, TH6202, None, 2, None],          # дата в будущем
        ["603", date(2026, 11, 13), None, TH6202, None, 2, 5],            # продано больше, чем в поставке
    ])
    preview = await upload(client, tokens, content, name="lots.xlsx")
    assert preview["counts"] == {"new": 1, "changed": 0, "unchanged": 0, "error": 4}
    docs = {d["number"]: d for d in preview["docs"]}
    assert docs["600"]["status"] == "new" and docs["600"]["lines"][0]["sold_before"] == 3
    assert "нужно целое число" in docs["601"]["lines"][0]["error"]
    assert docs[None]["lines"][0]["error"] == "Не указан № поставки"
    assert "позже сегодняшнего" in docs["602"]["errors"][0]
    assert "от 0 до 2" in docs["603"]["lines"][0]["error"]
    applied = await upload(client, tokens, content, name="lots.xlsx", dry_run=False)
    assert applied["counts"]["new"] == 1 and len((await call(client, tokens, "GET"))["lots"]) == 1

    # шаблон со своими поставками загружается обратно без ошибок и без изменений
    filled = await client.get(f"{URL}/lots/template", headers=auth(tokens))
    assert filled.status_code == 200
    back = await upload(client, tokens, filled.content, name="back.xlsx")
    assert back["counts"] == {"new": 0, "changed": 0, "unchanged": 1, "error": 0}
    # поменяли количество — новая версия поставки
    wb = openpyxl.load_workbook(io.BytesIO(filled.content))
    ws = wb.worksheets[0]
    header = [c.value for c in ws[1]]
    qty_col = next(i for i, h in enumerate(header) if h and h.startswith("Количество"))
    for row in ws.iter_rows(min_row=2):
        if row[header.index("Артикул")].value == TH6303:
            row[qty_col].value = 6
    out = io.BytesIO()
    wb.save(out)
    changed = await upload(client, tokens, out.getvalue(), name="back.xlsx", dry_run=False)
    assert changed["counts"]["changed"] == 1
    lt = (await call(client, tokens, "GET"))["lots"][0]
    assert lt["version"] == 2 and lt["qty"] == 16

    r = await client.post(f"{URL}/lots/upload", headers=auth(tokens),
                          files={"file": ("x.csv", "один;два\n1;2\n".encode(), "text/csv")})
    assert r.status_code == 400 and "Не найдены колонки" in r.json()["detail"]


async def test_sold_before_from_preview_field_and_other_store(client, tokens, sessions):
    await set_supplier(client, tokens)
    await import_reference_costs(client, tokens)
    body = await upload(client, tokens, PDF.read_bytes(), dry_run=False,
                        sold_before={f"587|2026-11-10|{TH6202}": 7})
    assert body["docs"][0]["lines"][3]["sold_before"] == 7
    lt = (await call(client, tokens, "GET"))["lots"][0]
    assert next(ln for ln in lt["lines"] if ln["article"] == TH6202)["remaining"] == 10
    # чужая поставка недоступна
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1",
                                                     "full_name": "Другой"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com",
                                                           "password": "strongpass1"})).json()
    await set_supplier(client, other)
    r = await client.post(f"{URL}/lots/{lt['lot_id']}/close", json={"reason": "other"}, headers=auth(other))
    assert r.status_code == 404
    r = await client.get(f"{URL}/lots/{lt['lot_id']}/document", headers=auth(other))
    assert r.status_code == 404
