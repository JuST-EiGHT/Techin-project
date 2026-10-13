"""Загрузка отчётов и история (шаг 2.2): сохранение, строки начислений, конфликты периода,
ручные правки, скрытие и восстановление, повторный разбор сохранённого файла."""
import hashlib
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import func, select

from app.integrations.xlsx import MappingCostProvider
from app.main import app
from app.models.accrual import SOURCE_XLSX, Accrual, AccrualType
from app.models.product import Product
from app.models.report import Report, ReportFile
from app.services.costs import get_cost_provider
from tests.conftest import reference_costs

FIXTURES = Path(__file__).parent / "fixtures"
ACCRUAL = "accrual_2026-09-07_13.xlsx"
REALIZATION = "realization_2026-08.xlsx"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def owner_costs(client):
    """Эталонные цифры BUSINESS_LOGIC §3 считаются с себестоимостью владельца (справочник — test_costs.py)."""
    costs = reference_costs()
    app.dependency_overrides[get_cost_provider] = lambda: costs


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def accrual_xlsx(period: str, rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Начисления"
    ws.append([period])
    ws.append(["ID начисления", "Дата начисления", "Тип начисления", "Артикул", "SKU", "Количество", "Сумма итого, руб."])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, tokens, name: str, content: bytes, **params):
    return client.post(
        "/api/v1/reports/upload", params=params, headers=auth(tokens), files={"file": (name, content, XLSX_TYPE)}
    )


async def xlsx_accruals(sessions, report_id=None) -> list[Accrual]:
    async with sessions() as db:
        q = select(Accrual).where(Accrual.source == SOURCE_XLSX)
        if report_id:
            q = q.where(Accrual.report_id == uuid.UUID(str(report_id)))
        return list((await db.execute(q.order_by(Accrual.line_no))).scalars())


async def test_reports_require_auth(client):
    for method, url in [("get", "/api/v1/reports"), ("post", "/api/v1/reports/upload")]:
        assert (await getattr(client, method)(url)).status_code == 401


async def test_upload_accrual_report_saves_summary_file_and_lines(client, tokens, sessions):
    content = fixture(ACCRUAL)
    r = await upload(client, tokens, ACCRUAL, content)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["report_type"], body["status"], body["filename"]) == ("accrual", "ready", ACCRUAL)
    assert (body["period_start"], body["period_end"]) == ("2026-09-07", "2026-09-13")
    assert (body["auto_total"], body["effective_total"], body["manual_total"]) == ("10955.83", "10955.83", None)
    assert (body["revenue_base"], body["cost_sales"]) == ("23712.03", "10143.36")
    assert (body["vat_multiplier"], body["tax_rate"]) == ("1.220", "0.2000")
    assert body["missing_cost_count"] == 0 and body["summary"]["by_type"]["Выручка"] == "23712.03"
    assert body["file_size"] == len(content)

    async with sessions() as db:
        report = await db.get(Report, uuid.UUID(body["id"]))
        stored = await db.get(ReportFile, report.id)
        assert stored.content == content and report.file_sha256 == hashlib.sha256(content).hexdigest()

    lines = await xlsx_accruals(sessions)
    assert len(lines) == 366
    assert sum(ln.amount for ln in lines) == Decimal("10955.83")  # строки = итог отчёта до копейки
    revenue = next(ln for ln in lines if ln.type_name == "Выручка")
    assert (revenue.posting_number, revenue.offer_id, revenue.sku, revenue.quantity) == (
        "08537754-0092-1", "6001-2RS.C3.SRL.NAC.TH", 3145353644, 1
    )
    assert revenue.accrual_date == date(2026, 9, 7) and revenue.amount == Decimal("482.66")
    assert revenue.sync_scope == f"report:{body['id']}" and revenue.raw_json["Группа услуг"] == "Продажи"
    assert revenue.accrual_type_id is None  # справочника типов Ozon (API) у пользователя нет


async def test_lines_linked_to_products_and_accrual_types(client, tokens, sessions):
    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    async with sessions() as db:
        db.add(Product(user_id=uuid.UUID(me["id"]), ozon_product_id=1, ozon_sku=3145353644, offer_id="6001-2RS.C3.SRL.NAC.TH",
                       synced_at=datetime.now(timezone.utc)))
        db.add(AccrualType(id=101, name_ru="Выручка"))
        await db.commit()
    body = (await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))).json()
    lines = await xlsx_accruals(sessions, body["id"])
    revenue = [ln for ln in lines if ln.offer_id == "6001-2RS.C3.SRL.NAC.TH" and ln.type_name == "Выручка"]
    assert revenue and all(ln.product_id is not None and ln.accrual_type_id == 101 for ln in revenue)


async def test_realization_report_has_no_accrual_lines(client, tokens, sessions):
    r = await upload(client, tokens, REALIZATION, fixture(REALIZATION))
    assert r.status_code == 201
    assert (r.json()["report_type"], r.json()["auto_total"], r.json()["rows_count"]) == ("realization", "37803.35", 44)
    assert await xlsx_accruals(sessions) == []


async def test_list_tabs_pagination_and_other_users(client, tokens, sessions):
    await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))
    await upload(client, tokens, "accrual_2026-08-01_09.xlsx", fixture("accrual_2026-08-01_09.xlsx"))
    await upload(client, tokens, REALIZATION, fixture(REALIZATION))
    get = lambda **q: client.get("/api/v1/reports", params=q, headers=auth(tokens))  # noqa: E731

    page = (await get()).json()
    assert page["total"] == 3
    weekly = (await get(report_type="accrual")).json()
    assert [i["period_start"] for i in weekly["items"]] == ["2026-09-07", "2026-08-01"]  # новые периоды — первыми
    assert "summary" not in weekly["items"][0]  # список без подробностей
    monthly = (await get(report_type="realization")).json()
    assert [i["report_type"] for i in monthly["items"]] == ["realization"]
    second = (await get(report_type="accrual", limit=1, offset=1)).json()
    assert second["total"] == 2 and second["items"][0]["period_start"] == "2026-08-01"
    assert (await get(report_type="weekly")).json()["detail"] == "Тип отчёта: недопустимое значение"

    # чужой пользователь не видит и не трогает отчёты
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    assert (await client.get("/api/v1/reports", headers=auth(other))).json()["total"] == 0
    report_id = page["items"][0]["id"]
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {"manual_total": "1"}} if method == "patch" else {}
        r = await getattr(client, method)(f"/api/v1/reports/{report_id}", headers=auth(other), **kwargs)
        assert (r.status_code, r.json()["detail"]) == (404, "Отчёт не найден")


async def test_same_file_and_same_period_ask_keep_replace(client, tokens, sessions):
    first = (await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))).json()

    r = await upload(client, tokens, "копия.xlsx", fixture(ACCRUAL))
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == 409 and body["detail"].startswith(f"Этот файл уже загружен: «{ACCRUAL}», 07.09.2026–13.09.2026")
    assert body["detail"].endswith("Заменить его новым или оставить оба?")
    assert [(c["id"], c["same_file"]) for c in body["conflicts"]] == [(first["id"], True)]

    # другой файл за пересекающийся период того же типа — тоже вопрос
    overlap = accrual_xlsx("Период: 10.09.2026-16.09.2026", [["1", "10.09.2026", "Логистика", "", "", 0, -74]])
    r = await upload(client, tokens, "week2.xlsx", overlap)
    assert r.status_code == 409 and r.json()["conflicts"][0]["same_file"] is False
    assert r.json()["detail"].startswith(f"За этот период уже есть отчёт «{ACCRUAL}» (07.09.2026–13.09.2026).")
    # отчёт другого типа за тот же месяц — не конфликт
    assert (await upload(client, tokens, REALIZATION, fixture(REALIZATION))).status_code == 201

    kept = await upload(client, tokens, "week2.xlsx", overlap, on_conflict="keep")
    assert kept.status_code == 201
    assert (await client.get("/api/v1/reports", params={"report_type": "accrual"}, headers=auth(tokens))).json()["total"] == 2

    replaced = await upload(client, tokens, "new.xlsx", fixture(ACCRUAL), on_conflict="replace")
    assert replaced.status_code == 201
    new_id = replaced.json()["id"]
    items = (await client.get("/api/v1/reports", params={"report_type": "accrual"}, headers=auth(tokens))).json()["items"]
    assert [i["id"] for i in items] == [new_id]  # оба пересекающихся заменены
    async with sessions() as db:
        old = await db.get(Report, uuid.UUID(first["id"]))
        assert old.deleted_at is not None and str(old.replaced_by_id) == new_id
        assert await db.get(ReportFile, old.id) is not None  # файл скрытого отчёта не удаляется
    assert await xlsx_accruals(sessions, first["id"]) == []
    assert len(await xlsx_accruals(sessions, new_id)) == 366


async def test_patch_manual_total_vat_and_tax(client, tokens):
    report_id = (await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))).json()["id"]
    patch = lambda body: client.patch(f"/api/v1/reports/{report_id}", json=body, headers=auth(tokens))  # noqa: E731

    body = (await patch({"manual_total": "11000.50", "vat_multiplier": "1.2", "tax_rate": "0.06"})).json()
    assert (body["manual_total"], body["effective_total"], body["auto_total"]) == ("11000.50", "11000.50", "10955.83")
    assert (body["vat_multiplier"], body["tax_rate"]) == ("1.200", "0.0600")
    # правки сохраняются: вернуться к отчёту позже
    again = (await client.get(f"/api/v1/reports/{report_id}", headers=auth(tokens))).json()
    assert again["manual_total"] == "11000.50"
    # не переданные поля не меняются; null — вернуться к авто и значениям по умолчанию
    body = (await patch({"manual_total": None})).json()
    assert (body["manual_total"], body["effective_total"], body["tax_rate"]) == (None, "10955.83", "0.0600")
    body = (await patch({"vat_multiplier": None, "tax_rate": None})).json()
    assert (body["vat_multiplier"], body["tax_rate"]) == ("1.220", "0.2000")

    for bad, text in [
        ({"vat_multiplier": "2.5"}, "Коэффициент НДС: не больше 2"),
        ({"tax_rate": "1"}, "Ставка налога: меньше 1"),
        ({"tax_rate": "-0.1"}, "Ставка налога: не меньше 0"),
        ({"manual_total": "abc"}, "Сумма от Ozon: нужно число"),
        ({"manual_total": "1.234"}, "Сумма от Ozon: не больше 2 знаков после запятой"),
    ]:
        r = await patch(bad)
        assert (r.status_code, r.json()["detail"]) == (400, text)


async def test_delete_hides_and_restore_brings_back(client, tokens, sessions):
    report_id = (await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))).json()["id"]
    await client.patch(f"/api/v1/reports/{report_id}", json={"manual_total": "10000"}, headers=auth(tokens))

    assert (await client.delete(f"/api/v1/reports/{report_id}", headers=auth(tokens))).status_code == 204
    assert (await client.get("/api/v1/reports", headers=auth(tokens))).json()["total"] == 0
    assert (await client.get(f"/api/v1/reports/{report_id}", headers=auth(tokens))).status_code == 404
    assert await xlsx_accruals(sessions) == []  # в расчёты скрытый отчёт не попадает
    async with sessions() as db:
        assert (await db.get(Report, uuid.UUID(report_id))).deleted_at is not None
        assert await db.get(ReportFile, uuid.UUID(report_id)) is not None

    r = await client.post(f"/api/v1/reports/{report_id}/restore", headers=auth(tokens))
    assert r.status_code == 200 and r.json()["manual_total"] == "10000.00"  # правки не теряются
    assert len(await xlsx_accruals(sessions)) == 366
    # восстановить можно только скрытый
    again = await client.post(f"/api/v1/reports/{report_id}/restore", headers=auth(tokens))
    assert (again.status_code, again.json()["detail"]) == (404, "Отчёт не найден")


async def test_reparse_uses_current_costs_and_keeps_manual_edits(client, tokens, sessions):
    report_id = (await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))).json()["id"]
    await client.patch(f"/api/v1/reports/{report_id}", json={"manual_total": "10000"}, headers=auth(tokens))

    app.dependency_overrides[get_cost_provider] = lambda: MappingCostProvider({})
    body = (await client.post(f"/api/v1/reports/{report_id}/reparse", headers=auth(tokens))).json()
    assert (body["cost_sales"], body["missing_cost_count"], body["manual_total"]) == ("0.00", 16, "10000.00")
    assert len(await xlsx_accruals(sessions)) == 366  # строки пересобраны, не задвоены

    # файл больше не читается (например, после смены формата) — итоги прежние, статус error
    async with sessions() as db:
        (await db.get(ReportFile, uuid.UUID(report_id))).content = b"broken"
        await db.commit()
    r = await client.post(f"/api/v1/reports/{report_id}/reparse", headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"].startswith("Не удалось открыть файл Excel")
    body = (await client.get(f"/api/v1/reports/{report_id}", headers=auth(tokens))).json()
    assert body["status"] == "error" and body["error_message"].startswith("Не удалось открыть файл Excel")
    assert body["auto_total"] == "10955.83" and len(await xlsx_accruals(sessions)) == 366


async def test_upload_validation_and_parse_errors_save_nothing(client, tokens, sessions, monkeypatch):
    cases = [
        ("old.xls", b"whatever", 400, "Файл в старом формате Excel (.xls)"),
        ("report.csv", b"a,b", 400, "Поддерживаются только файлы .xlsx"),
        ("junk.xlsx", b"not excel", 400, "Не удалось открыть файл Excel"),
        ("empty.xlsx", b"", 400, "Файл пустой."),
    ]
    for name, content, code, text in cases:
        r = await upload(client, tokens, name, content)
        assert r.status_code == code and r.json()["detail"].startswith(text), (name, r.text)
    stock = fixture("stock_management_2026-09-13.xlsx")
    r = await upload(client, tokens, "stock.xlsx", stock)
    assert r.status_code == 400 and r.json()["detail"].startswith("Не удалось определить тип отчёта Ozon")
    r = await client.post("/api/v1/reports/upload", headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, "Файл: обязательное поле")
    r = await upload(client, tokens, ACCRUAL, fixture(ACCRUAL), on_conflict="maybe")
    assert r.json()["detail"] == "Действие при совпадении периода: недопустимое значение"

    from app.core.config import settings
    monkeypatch.setattr(settings, "REPORT_MAX_SIZE_MB", 0)
    assert (await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))).status_code == 413

    async with sessions() as db:
        assert (await db.execute(select(func.count()).select_from(Report))).scalar_one() == 0


async def test_lines_without_date_use_report_period(client, tokens, sessions):
    content = accrual_xlsx("Период: 01.10.2026-07.10.2026", [
        ["1", None, "Выручка", "6001-2RS.C3.SRL.NAC.TH", "3145353644", 1, 500],
        ["2", "03.10.2026", "Логистика", "6001-2RS.C3.SRL.NAC.TH", "3145353644", 1, "-74,5"],
    ])
    body = (await upload(client, tokens, "week.xlsx", content)).json()
    lines = await xlsx_accruals(sessions, body["id"])
    assert [(ln.accrual_date, ln.amount) for ln in lines] == [
        (date(2026, 10, 1), Decimal("500.00")), (date(2026, 10, 3), Decimal("-74.50"))
    ]


async def test_finance_api_ignores_xlsx_lines(client, tokens):
    await upload(client, tokens, ACCRUAL, fixture(ACCRUAL))
    r = await client.get(
        "/api/v1/finance/accruals", params={"date_from": "2026-09-01", "date_to": "2026-09-30"}, headers=auth(tokens)
    )
    assert r.status_code == 200 and r.json()["total"] == 0  # начисления из API и из XLSX не смешиваются

