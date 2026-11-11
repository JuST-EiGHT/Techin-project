"""Шаг 4.2a — юнит-экономика как у Ozon: возвраты и невыкупы отдельно от логистики и выкуп, реклама по факту (своя у
товара + без товара по доле в продажах), хранение по отчёту Ozon «Стоимость размещения по товарам» (запрос → код →
/v1/report/info → файл → строки → доли хранения магазина за день), всё это — в прибыли по товарам и в расчёте цены."""
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx
from openpyxl import Workbook
from sqlalchemy import select

from app.core.config import settings
from app.engine.product_pnl import LineIn, compute, shares_for, split
from app.integrations.ozon.factory import get_ozon_client
from app.integrations.ozon_reports.placement import parse
from app.integrations.xlsx.common import ReportParseError
from app.models.accrual import Accrual, AccrualDay
from app.models.product import Product
from app.models.report import OzonDocument
from app.models.storage import PLACEMENT, StorageCost
from app.services import storage_report
from app.services.pnl_cache import cache as pnl_cache
from app.services.sync.finance import today_msk
from tests.test_pricing import auth, calc, seed, user_id_of
from tests.test_sync_overview import add_key

D = Decimal
DAY = date(2026, 10, 20)


def ln(type_name, amount, offer=None, qty=1, posting=None, day=DAY):
    return LineIn(day, type_name, D(amount), qty, offer, "", "", posting)


def no_cost(offer, day):
    return D(100), D(1)


# ── группы, выкуп, хранение по долям ──
def test_returns_ads_storage_and_buyout():
    lines = [
        # P1 — продан: продажи Ozon = выручка + баллы + партнёры (как в отчёте владельца: 1550 + 1490 + 16)
        ln("Выручка", "1550", "A", 1, "P1"), ln("Баллы за скидки", "1490", "A", 1, "P1"),
        ln("Программы партнёров", "16", "A", 1, "P1"), ln("Вознаграждение за продажу", "-1436", "A", 1, "P1"),
        ln("Эквайринг", "-29", "A", 1, "P1"), ln("Логистика", "-69", "A", 1, "P1"),
        ln("Доставка до места выдачи", "-11", "A", 1, "P1"), ln("Звёздные товары", "-46", "A", 1, "P1"),
        # P2 — невыкуп: доехал до покупателя и вернулся, выручки нет
        ln("Логистика", "-65", "A", 1, "P2"), ln("Обратная логистика", "-56", "A", 1, "P2"),
        ln("Обработка возвратов, отмен и невыкупов партнёрами", "-15", "A", 1, "P2"),
        # P3 — купили и вернули: это возврат, а не невыкуп
        ln("Выручка", "482", "A", 1, "P3"), ln("Возврат выручки", "-482", "A", 1, "P3"),
        ln("Обратная логистика", "-56", "A", 1, "P3"),
        # без товара: реклама по кампании и хранение магазина без отчёта о размещении
        ln("Оплата за клик", "-100"), ln("Размещение на складе", "-50"),
    ]
    r = compute(lines, {}, no_cost, False, D(1))
    a = r.rows[0]
    from app.engine.product_pnl import group_of
    assert group_of("Возврат баллов за скидки") == group_of("Возврат партнёрских выплат") == "other"  # сторона продаж
    assert (a.qty, a.returned_qty, a.not_bought, a.buyout_pct) == (2, 1, 1, D("66.7"))
    assert (a.logistics, a.returns, a.ads, a.storage) == (D(-145), D(-127), D(-46), D(0))
    assert a.sales == D(1550 + 1490 + 16 + 482) and a.revenue == D(2032)
    # прочее — эквайринг, баллы, партнёры и возврат выручки; реклама и возвраты в нём больше не лежат
    assert a.other == D(-29 + 1490 + 16 - 482)
    assert r.unallocated.by_group == {"ads": D(-100), "storage": D(-50)}
    assert sum((x.total for x in r.rows), r.unallocated.total) == r.period_total


def test_storage_split_by_report_shares():
    lines = [ln("Выручка", "1000", "A", 1, "P1"), ln("Размещение на складе", "-50"),
             ln("Размещение на складе", "-10", day=DAY + timedelta(days=1)),
             ln("Размещение на складе", "-7", day=DAY + timedelta(days=9))]
    # отчёт за день DAY: A — 30 ₽ места, B — 10 ₽ (B не продавался — строка появится только хранением)
    r = compute(lines, {}, no_cost, False, D(1), {DAY: {"A": D(30), "B": D(10)}})
    rows = {x.offer_id: x for x in r.rows}
    assert (rows["A"].storage, rows["B"].storage) == (D("-37.50") + D("-7.50"), D("-12.50") + D("-2.50"))
    assert rows["B"].storage_by_report == rows["B"].storage and rows["B"].cost == 0 and rows["B"].qty == 0
    # день +1 — доли ближайшего дня отчёта; день +9 — дальше 3 дней: не делим, «Не распределено»
    assert r.unallocated.by_group == {"storage": D(-7)}
    assert sum((x.total for x in r.rows), r.unallocated.total) == r.period_total
    assert shares_for({DAY: {"A": D(1)}}, DAY + timedelta(days=4)) is None
    # копейки: части в сумме — ровно исходная сумма
    assert split(D(-10), {"A": D(1), "B": D(1), "C": D(1)}) == [("A", D("-3.34")), ("B", D("-3.33")), ("C", D("-3.33"))]


# ── разбор отчёта о размещении ──
def xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


PLACEMENT_XLSX = xlsx([
    ["Отчёт о стоимости размещения по товарам"], ["Период: 01.10.2026 – 31.10.2026"], [],
    ["Дата", "SKU", "Артикул", "Название товара", "Объём, л", "Количество, шт", "Стоимость размещения, руб."],
    ["20.10.2026", 501, "ART-A", "Подшипник A", 1.5, 4, "30,00"],
    ["20.10.2026", 502, None, "Подшипник B", 0.5, 2, 10],
    ["21.10.2026", 501, "ART-A", "Подшипник A", 1.5, 4, 12.5],
    ["Итого", None, None, None, None, None, 52.5],
])


def test_parse_placement_xlsx_and_csv():
    rows = parse(PLACEMENT_XLSX)
    assert [(r.day, r.sku, r.offer_id, r.amount) for r in rows] == [
        (date(2026, 10, 20), 501, "ART-A", D(30)), (date(2026, 10, 20), 502, None, D(10)),
        (date(2026, 10, 21), 501, "ART-A", D("12.5"))]
    # CSV из кабинета: «;», Windows-1251, без дат — сумма за весь период
    csv = "Артикул;SKU;Количество, шт;Сумма начисления, руб\nART-A;501;3;-120,50\nART-B;502;1;0\n".encode("cp1251")
    assert [(r.day, r.offer_id, r.amount) for r in parse(csv)] == [(None, "ART-A", D("120.50"))]
    with pytest.raises(ReportParseError, match="Стоимость размещения"):
        parse(xlsx([["Склад", "Объём, л"], ["Тверь", 10]]))
    with pytest.raises(ReportParseError, match="нет строк"):
        parse(xlsx([["SKU", "Стоимость размещения"], [501, 0]]))


def test_plan_windows():
    today = date(2026, 11, 10)
    first = storage_report.plan(today, [])
    # последние 31 день и недостающие дни за 90 — окнами по 31 день от новых
    assert first == [(date(2026, 10, 10), date(2026, 11, 9)), (date(2026, 9, 9), date(2026, 10, 9)),
                     (date(2026, 8, 12), date(2026, 9, 8))]
    now = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)
    docs = [OzonDocument(doc_type=PLACEMENT, period_start=a, period_end=b, status="ready", requested_at=now)
            for a, b in first]
    assert storage_report.plan(today, docs) == []  # сегодня уже просили, история закрыта
    # назавтра — только последние 31 день
    assert storage_report.plan(today + timedelta(days=1), docs) == [(date(2026, 10, 11), date(2026, 11, 10))]


# ── загрузка отчёта о размещении вместе с начислениями ──
FILE_URL = "https://cdn1.ozone.ru/s3/reports/placement.xlsx"


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield mock


async def test_sync_requests_downloads_and_parses(client, tokens, sessions, ozon):
    today = today_msk()
    yesterday = today - timedelta(days=1)
    uid = await user_id_of(client, tokens)
    await add_key(sessions, uid)
    async with sessions() as db:  # в каталоге — только ART-A (SKU 501)
        db.add(Product(user_id=uid, ozon_product_id=1, ozon_sku=501, offer_id="ART-A", synced_at=datetime.now(timezone.utc)))
        await db.commit()
    codes = iter(f"REPORT_placement_{n}" for n in range(1, 10))
    created = ozon.post(storage_report.CREATE_PATH).mock(
        side_effect=lambda request: httpx.Response(200, json={"code": next(codes)}))
    info = ozon.post("/v1/report/info").mock(
        return_value=httpx.Response(200, json={"result": {"status": "success", "file": FILE_URL}}))
    with respx.mock(assert_all_called=True) as files:
        files.get(FILE_URL).mock(return_value=httpx.Response(200, content=PLACEMENT_XLSX))
        async with sessions() as db:
            async with await get_ozon_client(uid, db) as c:
                stats = await storage_report.sync(db, uid, c, poll_delay=0)
    # первый раз — последние 31 день и ещё два окна истории; Ozon даёт 5 в день — просим 3
    assert stats == {"requested": 3, "ready": 3, "rows": 9}
    assert json.loads(created.calls[0].request.content) == {
        "date_from": (yesterday - timedelta(days=30)).isoformat(), "date_to": yesterday.isoformat()}
    assert info.call_count == 3
    async with sessions() as db:  # строки файла — за 20 и 21 октября: их покрывает окно, которое их захватывает
        for doc in (await db.execute(select(OzonDocument))).scalars():
            doc.period_start, doc.period_end = date(2026, 10, 1), date(2026, 10, 31)
        await db.commit()
        got = await storage_report.shares(db, uid, date(2026, 10, 20), date(2026, 10, 21))
    # SKU 502 без артикула — товара с таким SKU в каталоге нет: не угадываем и «товар-призрак» не заводим
    assert got == {date(2026, 10, 20): {"ART-A": D(30)}, date(2026, 10, 21): {"ART-A": D("12.5")}}

    # в тот же день повторно не просим; отчёт не готов — заберём при следующей синхронизации
    async with sessions() as db:
        async with await get_ozon_client(uid, db) as c:
            assert (await storage_report.sync(db, uid, c, poll_delay=0))["requested"] == 0
    # в списке документов Ozon (10.12) их нет — сервис просит их сам
    assert (await client.get("/api/v1/reports/documents", headers=auth(tokens))).json() == []


async def test_bad_file_kept_and_reparsed(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    async with sessions() as db:
        doc = OzonDocument(user_id=uid, doc_type=PLACEMENT, period_start=DAY, period_end=DAY, status="ready",
                           requested_at=datetime.now(timezone.utc), content=b"not a report")
        db.add(doc)
        await db.commit()
        assert await storage_report.ingest(db, doc) == 0
        assert doc.status == "error" and doc.error.startswith("Не удалось разобрать отчёт о размещении")
        assert doc.content == b"not a report"  # файл не выбрасываем — разберём, когда парсер научится
        doc.content = PLACEMENT_XLSX
        assert await storage_report.ingest(db, doc) == 3 and doc.status == "ready" and doc.error is None


# ── в расчёте цены и прибыли по товарам ──
async def add_accrual(db, uid, type_name, amount, day, product=None, qty=1, posting=None):
    if await db.get(AccrualDay, (uid, day)) is None:
        db.add(AccrualDay(user_id=uid, day=day, synced_at=datetime.now(timezone.utc), entries=1))
    db.add(Accrual(user_id=uid, product_id=product.id if product else None, type_name=type_name, accrual_date=day,
                   amount=D(amount), quantity=qty, source="api", posting_number=posting,
                   sync_scope=f"{type_name}:{posting}:{day}:{amount}", line_no=1))


async def test_price_uses_returns_ads_and_storage_facts(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)  # A: 4 продажи по 459 ₽ за 30 дней, логистика 60; B: 2 × 400, логистика 30
    a, b = p["A"], p["B"]
    day = today_msk() - timedelta(days=2)
    async with sessions() as db:
        # A: «Звёздные товары» 36,72 ₽ = 2 % его продаж (1836 ₽); невыкуп — обратная логистика 50 и обработка 10
        await add_accrual(db, uid, "Звёздные товары", "-36.72", day, a)
        await add_accrual(db, uid, "Обратная логистика", "-50", day, a, posting="NB-1")
        await add_accrual(db, uid, "Обработка возвратов, отмен и невыкупов партнёрами", "-10", day, a, posting="NB-1")
        # без товара: оплата за клик 131,80 ₽ = 5 % продаж магазина (2636 ₽), хранение магазина 66 ₽ за день
        await add_accrual(db, uid, "Оплата за клик", "-131.80", day, qty=0)
        await add_accrual(db, uid, "Размещение на складе", "-66", day, qty=0)
        await db.commit()
    await pnl_cache.invalidate(uid)

    d = await calc(client, tokens, a.id)
    i, f = d["inputs"], d["inputs"]["facts"]
    # реклама: своя 2 % + без товара 5 % — делим по доле в продажах
    assert (D(i["ad_pct"]), D(f["ad_own_pct"]), D(f["ad_shared_pct"])) == (7, 2, 5)
    assert i["sources"]["ad_pct"] == "по начислениям за 30 дн."
    # возвраты: 60 ₽ на 4 проданные штуки = 15 ₽; выкуп 4 из 5 доехавших
    assert (D(i["returns"]), f["not_bought"], f["buyout_pct"]) == (15, 1, "80.0")
    # хранения по отчёту нет — среднее по магазину: 66 ₽ / 6 проданных = 11 ₽, оценка
    assert D(i["storage"]) == 11 and "оценка" in i["sources"]["storage"]
    # α = 1 − 0,38 − 0,015 − 0,07 = 0,535; β = 200,08 + 60 + 15 + 11 = 286,08 → 534,73
    r = d["result"]
    assert r["min_price"] == "534.73" and r["current"]["returns"] == "15.00" and r["current"]["ad"] == "42.00"

    # отчёт о размещении: в этот день место A стоило 2 ₽, B — 1 ₽ → хранение магазина 66 = 44 (A) + 22 (B)
    async with sessions() as db:
        doc = OzonDocument(user_id=uid, doc_type=PLACEMENT, period_start=day, period_end=day, status="ready",
                           requested_at=datetime.now(timezone.utc), ready_at=datetime.now(timezone.utc))
        db.add(doc)
        await db.flush()
        db.add_all([StorageCost(user_id=uid, document_id=doc.id, day=day, offer_id="ART-A", amount=D(2)),
                    StorageCost(user_id=uid, document_id=doc.id, day=day, sku=502, amount=D(1))])  # B — по SKU
        await db.commit()
    await pnl_cache.invalidate(uid)
    i = (await calc(client, tokens, a.id))["inputs"]
    assert D(i["storage"]) == 11 and i["sources"]["storage"] == "по отчёту Ozon о размещении за 30 дн."  # 44 / 4
    assert D((await calc(client, tokens, b.id))["inputs"]["storage"]) == 11  # 22 / 2

    rows = (await client.get("/api/v1/pnl/by-product", params={
        "date_from": day.isoformat(), "date_to": day.isoformat()}, headers=auth(tokens))).json()
    by = {x["offer_id"]: x for x in rows["items"]}
    assert (by["ART-A"]["storage"], by["ART-B"]["storage"], rows["storage"]["has_report"]) == ("-44.00", "-22.00", True)
    assert by["ART-A"]["returns"] == "-60.00" and by["ART-A"]["ads"] == "-36.72" and by["ART-A"]["not_bought"] == 1
    assert rows["unallocated"]["by_group"] == {"ads": "-131.80"}  # реклама кампании — не делим в прибыли по товарам
    assert by["ART-A"]["per_unit"]["storage"] == "-44.00"  # в этот день A продан 1 раз
    # B в этот день не продавался — в таблице только хранением; название — из каталога, на штуку не делим
    assert (by["ART-B"]["qty"], by["ART-B"]["name"], by["ART-B"]["per_unit"]) == (0, "Подшипник B", None)
    # исходные строки товара сходятся с его суммой: хранение — долей строки магазина по отчёту
    lines = (await client.get("/api/v1/analytics/profit/lines", params={
        "date_from": day.isoformat(), "date_to": day.isoformat(), "offer_id": "ART-B"}, headers=auth(tokens))).json()
    assert (lines["total"], lines["lines"][0]["amount"], lines["lines"][0]["by_report"]) == ("-22.00", "-22.00", True)


async def test_ad_setting_only_without_data(client, tokens, sessions):
    """Настройка «реклама %» — когда данных о продажах нет совсем; своя у товара — важнее факта."""
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    await client.patch("/api/v1/settings", json={"price_ad_pct": "9"}, headers=auth(tokens))
    assert D((await calc(client, tokens, p["A"].id))["inputs"]["ad_pct"]) == 0  # продажи есть — рекламы нет
    r = await client.put(f"/api/v1/price/extras/{p['A'].id}", json={"ad_pct": "4"}, headers=auth(tokens))
    assert r.status_code == 200 and D(r.json()["ad_pct"]) == 4
    assert D((await calc(client, tokens, p["A"].id))["inputs"]["ad_pct"]) == 4
    # товар без продаж за 90 дней в магазине с продажами — средняя по магазину (0), а не настройка
    c = (await calc(client, tokens, p["C"].id))["inputs"]
    assert D(c["ad_pct"]) == 0 and "товар не продавался" in c["sources"]["ad_pct"]


async def test_extras_for_all_products(client, tokens, sessions):
    """Правка 11.11.2026: расходы из «Своих расходов» — для этого товара или для всех; своё у товара важнее общего, общее
    — важнее начислений; пустое — убрать."""
    uid = await user_id_of(client, tokens)
    p = await seed(sessions, uid)
    r = await client.put("/api/v1/price/extras-all", json={"other": "15", "ad_pct": "5"}, headers=auth(tokens))
    assert r.status_code == 200 and (D(r.json()["other"]), D(r.json()["ad_pct"]), r.json()["storage"]) == (15, 5, None)
    d = await calc(client, tokens, p["A"].id)
    i = d["inputs"]
    assert (D(i["ad_pct"]), D(i["other"])) == (5, 15)
    assert i["sources"]["ad_pct"] == i["sources"]["other"] == "для всех товаров" and D(i["for_all"]["other"]) == 15
    # α = 1 − 0,38 − 0,015 − 0,05 = 0,555; β = 200,08 + 60 + 15 = 275,08 → 495,64
    assert d["result"]["min_price"] == "495.64"
    assert D((await calc(client, tokens, p["B"].id))["inputs"]["other"]) == 15  # и у других товаров
    # своё у товара важнее общего: реклама 3 % у A, прочее — по-прежнему общее
    await client.put(f"/api/v1/price/extras/{p['A'].id}", json={"ad_pct": "3"}, headers=auth(tokens))
    d = await calc(client, tokens, p["A"].id)
    assert (D(d["inputs"]["ad_pct"]), d["inputs"]["sources"]["ad_pct"], d["result"]["min_price"]) == (
        3, "сохранено для товара", "478.40")  # 275,08 / 0,575
    s = (await client.get("/api/v1/settings", headers=auth(tokens))).json()
    assert (D(s["price_other_rub"]), D(s["price_ad_all_pct"]), s["price_storage_rub"]) == (15, 5, None)
    # убрать — снова начисления: реклама у B по факту 0, прочее 0
    assert (await client.put("/api/v1/price/extras-all", json={}, headers=auth(tokens))).json() == {
        "ad_pct": None, "storage": None, "other": None, "extra_pct": None}
    b = (await calc(client, tokens, p["B"].id))["inputs"]
    assert (D(b["ad_pct"]), D(b["other"]), b["sources"]["ad_pct"]) == (0, 0, "по начислениям за 30 дн.")
    bad = await client.put("/api/v1/price/extras-all", json={"ad_pct": "95"}, headers=auth(tokens))
    assert bad.status_code == 400
