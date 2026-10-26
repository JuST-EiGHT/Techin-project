"""Шаг 10.12: отчёты Ozon через API и документы — разбор `/v2/finance/realization` на обезличенной фикстуре, получение
через API как отчёт (исходный ответ, перечитывание, конфликт месяца и замена, ошибки простыми словами), документы файлом
через `/v1/report/info` (ждём, готов, ошибка, чужая ссылка, 3 дня), полнота по неделям и месяцам, каталог типов."""
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings
from app.engine import report_coverage as rc
from app.integrations.ozon_reports import parse_realization_api
from app.integrations.xlsx import ReportParseError
from app.integrations.xlsx.costs import MappingCostProvider
from app.models.accrual import AccrualDay
from app.models.report import OzonDocument
from app.services import ozon_reports
from tests.test_products import user_id_of
from tests.test_report_pnl import WEEK, auth, upload
from tests.test_sync_overview import add_key

D = Decimal
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ozon_api" / "realization_2026-09.json").read_text())
TODAY = date(2026, 10, 23)


def test_parse_realization_api():
    data = parse_realization_api(FIXTURE, MappingCostProvider({"A-1": D(100), "B-2": D(200)})).data
    assert (data.report_type, data.period, data.period_start, data.period_end) == (
        "realization", "01.09.2026–30.09.2026", date(2026, 9, 1), date(2026, 9, 30))
    # реализовано за вычетом возвратов: 10 000 + 2 500 + 600 − 1 000; партнёры: (300+50+10) − (30+5+1) + 25
    assert (data.revenue_base, data.auto_total) == (D("12100.00"), D("12449.00"))
    assert data.by_type == {"Итого реализовано (за вычетом возвратов)": D("12100.00"), "Всего выплат от партнёров": D("349.00")}
    assert data.cost_sales == D("2000.00") and [m.article for m in data.missing_cost] == ["C-3"]  # не 0 (правило 5)
    assert (data.by_article["A-1"].qty, data.by_article["A-1"].revenue, data.rows_count) == (10, D("10000.00"), 3)
    with pytest.raises(ReportParseError, match="нет строк"):
        parse_realization_api({"result": {"header": {}, "rows": []}}, MappingCostProvider({}))


def test_coverage_weeks_and_months():
    days = {date(2026, 9, d): ("xlsx" if d <= 13 else "api" if d <= 20 else None) for d in range(1, 31)}
    months = rc.months(days, {(2026, 9): [("api", "r1")]}, TODAY, 3)
    oct_, sep, aug = months
    assert [m.title for m in months] == ["Октябрь 2026", "Сентябрь 2026", "Август 2026"]
    assert (sep.realization, sep.realization_sources, oct_.realization, aug.realization) == ("have", ["api"], "current",
                                                                                          "missing")
    states = [(w.start.day, w.end.day, w.state) for w in sep.weeks]
    assert states == [(1, 6, "xlsx"), (7, 13, "xlsx"), (14, 20, "api"), (21, 27, "none"), (28, 30, "none")]
    assert sep.missing == 10 and oct_.weeks[-1].state == "future"  # после сегодняшнего — не ждём
    # отчёт за прошлый месяц Ozon формирует до 5-го — раньше это не дыра
    assert rc.realization_state(2026, 9, date(2026, 10, 3), False) == "not_yet"
    assert rc.realization_state(2026, 9, date(2026, 10, 6), False) == "missing"


@pytest.fixture
def ozon():
    with respx.mock(base_url=settings.OZON_API_URL, assert_all_called=False) as mock:
        yield mock


async def test_fetch_realization_as_report(client, tokens, sessions, ozon, monkeypatch):
    monkeypatch.setattr(ozon_reports, "today_msk", lambda: TODAY)
    for offer, cost in (("A-1", "100"), ("B-2", "200")):
        await client.put(f"/api/v1/costs/{offer}", json={"cost_price": cost, "vat_multiplier": "1"}, headers=auth(tokens))
    fetch = lambda **kw: client.post("/api/v1/reports/fetch", json={"year": 2026, "month": 9, **kw},  # noqa: E731
                                     headers=auth(tokens))
    r = await fetch()
    assert r.status_code == 400 and "API-ключ" in r.json()["detail"]  # без ключа — понятно, что сделать

    await add_key(sessions, await user_id_of(client, tokens))
    route = ozon.post("/v2/finance/realization").mock(return_value=httpx.Response(200, json=FIXTURE))
    r = await fetch()
    assert r.status_code == 201, r.text
    report = r.json()
    assert json.loads(route.calls[0].request.content) == {"month": 9, "year": 2026}
    assert (report["source"], report["report_type"], report["period_start"], report["revenue_base"]) == (
        "api", "realization", "2026-09-01", "12100.00")
    assert report["cost_sales"] == "2000.00" and report["missing_cost_count"] == 1
    # исходный ответ сохранён — перечитывается тем же разбором
    again = await client.post(f"/api/v1/reports/{report['id']}/reparse", headers=auth(tokens))
    assert again.status_code == 200 and again.json()["auto_total"] == "12449.00"
    listed = (await client.get("/api/v1/reports", params={"report_type": "realization"}, headers=auth(tokens))).json()
    assert [i["source"] for i in listed["items"]] == ["api"]

    # повтор за тот же месяц — выбор, как при загрузке файла; «заменить» — прежний скрыт
    conflict = await fetch()
    assert conflict.status_code == 409 and conflict.json()["conflicts"][0]["same_file"] is True
    replaced = await fetch(on_conflict="replace")
    assert replaced.status_code == 201
    listed = (await client.get("/api/v1/reports", params={"report_type": "realization"}, headers=auth(tokens))).json()
    assert [i["id"] for i in listed["items"]] == [replaced.json()["id"]]

    # ошибки простыми словами
    ozon.post("/v2/finance/realization").mock(return_value=httpx.Response(404, json={"message": "not found"}))
    r = await client.post("/api/v1/reports/fetch", json={"year": 2026, "month": 8}, headers=auth(tokens))
    assert r.status_code == 404 and "до 5-го числа" in r.json()["detail"]
    r = await client.post("/api/v1/reports/fetch", json={"year": 2026, "month": 10}, headers=auth(tokens))
    assert r.status_code == 400 and "когда месяц закончится" in r.json()["detail"]
    r = await client.post("/api/v1/reports/fetch", json={"year": 2023, "month": 7}, headers=auth(tokens))
    assert r.status_code == 400 and "с 08.2023" in r.json()["detail"]

    feed = (await client.get("/api/v1/analytics/events", params={"kind": "action"}, headers=auth(tokens))).json()
    assert any(i["title"].startswith("Получен через API отчёт за 01.09.2026–30.09.2026") for i in feed["items"])


FILE_URL = "https://ir.ozone.ru/s3/reports/mutual.xlsx"


async def test_documents_request_poll_download(client, tokens, sessions, ozon, monkeypatch):
    monkeypatch.setattr(ozon_reports, "today_msk", lambda: TODAY)
    uid = await user_id_of(client, tokens)
    await add_key(sessions, uid)
    created = ozon.post("/v1/finance/mutual-settlement").mock(
        return_value=httpx.Response(200, json={"result": {"code": "REPORT_mutual_1"}}))
    info = ozon.post("/v1/report/info")
    info.mock(return_value=httpx.Response(200, json={"result": {"code": "REPORT_mutual_1", "status": "processing"}}))

    r = await client.post("/api/v1/reports/documents", json={"doc_type": "mutual_settlement", "year": 2026, "month": 9},
                          headers=auth(tokens))
    assert r.status_code == 201, r.text
    doc = r.json()
    assert json.loads(created.calls[0].request.content) == {"date": "2026-09", "language": "RU"}
    assert (doc["status"], doc["title"], doc["period_start"]) == ("requested", "Отчёт о взаиморасчётах", "2026-09-01")

    r = await client.post(f"/api/v1/reports/documents/{doc['id']}/refresh", headers=auth(tokens))
    assert (r.json()["status"], r.json()["state_text"]) == ("requested", "Ozon формирует")

    info.mock(return_value=httpx.Response(200, json={"result": {"status": "success", "file": FILE_URL}}))
    with respx.mock(assert_all_called=True) as files:
        files.get(FILE_URL).mock(return_value=httpx.Response(200, content=b"PK-xlsx-bytes"))
        r = await client.post(f"/api/v1/reports/documents/{doc['id']}/refresh", headers=auth(tokens))
    ready = r.json()
    assert (ready["status"], ready["file_name"], ready["file_size"]) == ("ready", "Отчёт о взаиморасчётах 09.2026.xlsx", 13)
    f = await client.get(f"/api/v1/reports/documents/{doc['id']}/file", headers=auth(tokens))
    assert f.status_code == 200 and f.content == b"PK-xlsx-bytes" and "attachment" in f.headers["content-disposition"]
    listed = (await client.get("/api/v1/reports/documents", headers=auth(tokens))).json()
    assert [d["status"] for d in listed] == ["ready"]

    # Ozon не смог; ссылка не со своего сайта — не скачиваем; код старше 3 дней — запросить заново
    ozon.post("/v1/finance/compensation").mock(return_value=httpx.Response(200, json={"result": {"code": "C1"}}))
    comp = (await client.post("/api/v1/reports/documents", json={"doc_type": "compensation", "year": 2026, "month": 9},
                              headers=auth(tokens))).json()
    info.mock(return_value=httpx.Response(200, json={"result": {"status": "failed", "error": "INTERNAL"}}))
    failed = (await client.post(f"/api/v1/reports/documents/{comp['id']}/refresh", headers=auth(tokens))).json()
    assert failed["status"] == "error" and failed["error"].startswith("Ozon не смог сформировать документ (INTERNAL)")

    ozon.post("/v1/finance/decompensation").mock(return_value=httpx.Response(200, json={"result": {"code": "D1"}}))
    dec = (await client.post("/api/v1/reports/documents", json={"doc_type": "decompensation", "year": 2026, "month": 9},
                             headers=auth(tokens))).json()
    info.mock(return_value=httpx.Response(200, json={"result": {"status": "success", "file": "https://evil.example/x"}}))
    bad = (await client.post(f"/api/v1/reports/documents/{dec['id']}/refresh", headers=auth(tokens))).json()
    assert bad["status"] == "error" and "не со своего сайта" in bad["error"]

    async with sessions() as db:
        old = OzonDocument(user_id=uid, doc_type="mutual_settlement", period_start=date(2026, 8, 1),
                           period_end=date(2026, 8, 31), status="requested", ozon_code="OLD",
                           requested_at=ozon_reports._now() - timedelta(days=4))
        db.add(old)
        await db.commit()
        old_id = old.id
    expired = (await client.post(f"/api/v1/reports/documents/{old_id}/refresh", headers=auth(tokens))).json()
    assert expired["status"] == "error" and "за 3 дня" in expired["error"]

    no_file = await client.get(f"/api/v1/reports/documents/{comp['id']}/file", headers=auth(tokens))
    assert no_file.status_code == 404
    r = await client.post("/api/v1/reports/documents", json={"doc_type": "compensation", "year": 2026, "month": 10},
                          headers=auth(tokens))
    assert r.status_code == 400


async def test_coverage_api_and_catalog(client, tokens, sessions, monkeypatch):
    uid = await user_id_of(client, tokens)
    await upload(client, tokens, WEEK)  # отчёт по начислениям 07–13.09.2026
    async with sessions() as db:
        for d in range(14, 21):
            db.add(AccrualDay(user_id=uid, day=date(2026, 9, d), synced_at=ozon_reports._now(), entries=1))
        await db.commit()
    monkeypatch.setattr(ozon_reports, "today_msk", lambda: TODAY)
    body = (await client.get("/api/v1/reports/coverage", params={"months": 2}, headers=auth(tokens))).json()
    sep = body["months"][1]
    assert (sep["title"], sep["realization"]) == ("Сентябрь 2026", "missing")
    weeks = {w["date_from"]: w for w in sep["weeks"]}
    assert weeks["2026-09-07"]["state"] == "xlsx" and weeks["2026-09-14"]["state"] == "api"
    assert weeks["2026-09-01"]["missing_ranges"] == [{"date_from": "2026-09-01", "date_to": "2026-09-06"}]
    assert sep["missing"] == 30 - 14

    analytics = (await client.get("/api/v1/reports/catalog", headers=auth(tokens))).json()
    finance = (await client.get("/api/v1/reports/catalog", params={"scope": "finance"}, headers=auth(tokens))).json()
    keys = [k["key"] for k in analytics]
    assert {"accrual", "realization", "mutual_settlement", "products", "stocks", "postings"} <= set(keys)
    assert all(k["finance"] for k in finance) and "products" not in [k["key"] for k in finance]
    services = next(k for k in finance if k["key"] == "services_documents")
    assert services["how"] == ["cabinet"] and services["how_titles"] == ["Только в личном кабинете"]
    realization = next(k for k in finance if k["key"] == "realization")
    assert realization["how"] == ["button", "upload"] and realization["api"] == ["/v2/finance/realization"]
