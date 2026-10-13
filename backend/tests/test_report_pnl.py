"""P&L в отчётах через API (шаг 3.1): налог по профилю на дату периода, поправки отчёта, НДС артикулов."""

from pathlib import Path

from tests.conftest import REFERENCE_COSTS_CSV

FIXTURES = Path(__file__).parent / "fixtures"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WEEK, MONTH = "accrual_2026-09-07_13.xlsx", "realization_2026-08.xlsx"
OWNER = {"regime": "custom", "rate_pct": "20", "custom_base": "profit"}  # параметры владельца — явно


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def upload(client, tokens, name):
    r = await client.post(
        "/api/v1/reports/upload",
        headers=auth(tokens),
        files={"file": (name, (FIXTURES / name).read_bytes(), XLSX_TYPE)},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def import_reference_costs(client, tokens):
    r = await client.post(
        "/api/v1/costs/import",
        params={"dry_run": False},
        headers=auth(tokens),
        files={"file": ("cost_prices.csv", REFERENCE_COSTS_CSV.read_bytes(), "text/csv")},
    )
    assert r.json()["counts"]["new"] == 78


async def set_profile(client, tokens, body):
    assert (await client.put("/api/v1/tax-profile", json=body, headers=auth(tokens))).status_code == 200


async def test_reference_reports_with_owner_profile(client, tokens):
    await import_reference_costs(client, tokens)
    week = (await upload(client, tokens, WEEK))["pnl"]
    # налог не задан — остаток и маржа не считаются, а не 0
    assert (week["tax"], week["vat"], week["net"], week["margin"]) == (None, None, None, None)
    assert (week["owed"], week["profit_before_tax"]) == ("12374.90", "-1419.07")
    assert any(w.startswith("Налог не задан") for w in week["warnings"]) and week["tax_title"] is None

    await set_profile(client, tokens, OWNER)
    month = await upload(client, tokens, MONTH)
    p = month["pnl"]
    assert (p["owed"], p["profit_before_tax"], p["tax"], p["net"], p["margin"]) == (
        "17915.57",
        "19887.78",
        "3977.56",
        "15910.23",
        "42.47",
    )
    assert p["tax_title"] == "Свой процент: 20 % от прибыли" and p["data_source"] == "xlsx"
    assert sum(float(p["by_type_pct"][k]) for k in p["by_type_pct"]) == 100.0  # только доходы
    items = (await client.get("/api/v1/reports", headers=auth(tokens))).json()["items"]
    assert {i["period"]: i["pnl"]["net"] for i in items} == {
        month["period"]: "15910.23",
        "07.09.2026-13.09.2026": "-1419.07",
    }


async def test_manual_total_and_report_overrides(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    report = await upload(client, tokens, MONTH)
    url = f"/api/v1/reports/{report['id']}"
    patch = lambda body: client.patch(url, json=body, headers=auth(tokens))  # noqa: E731

    p = (await patch({"manual_total": "37000"})).json()["pnl"]
    assert (p["effective_total"], p["tax"], p["net"]) == ("37000.00", "3816.89", "15267.55")
    # «свой процент» для этого отчёта: 6 % от дохода (выручки)
    p = (await patch({"tax_rate": "0.06", "tax_base": "revenue"})).json()["pnl"]
    assert p["tax"] == "2247.70" and p["tax_title"] == "Свой процент: 6 % от дохода — поправка в этом отчёте"
    p = (await patch({"tax_rate": None, "tax_base": None})).json()["pnl"]
    assert p["tax"] == "3816.89" and p["tax_title"] == "Свой процент: 20 % от прибыли"  # снова по профилю
    # у артикулов своя ставка НДС в справочнике (1,22) — она важнее коэффициента отчёта
    p = (await patch({"vat_multiplier": "1"})).json()["pnl"]
    assert p["owed"] == "17915.57" and p["supplier_vat"] == "1.000"


async def test_report_vat_applies_to_articles_without_own_rate(client, tokens):
    await set_profile(client, tokens, OWNER)
    await client.put("/api/v1/costs/6001-2RS.C3.SRL.NAC.TH", json={"cost_price": "164"}, headers=auth(tokens))
    report = await upload(client, tokens, WEEK)
    assert report["pnl"]["owed"] == "400.16"  # 164 × 2 шт. × 1,22 — НДС поставщика по умолчанию
    url = f"/api/v1/reports/{report['id']}"
    p = (await client.patch(url, json={"vat_multiplier": "1"}, headers=auth(tokens))).json()["pnl"]
    assert p["owed"] == "328.00"  # поставщик без НДС — для этого отчёта
    await client.patch(url, json={"vat_multiplier": None}, headers=auth(tokens))
    # НДС поставщика 10 % в профиле с сентября; без даты профиль действовал бы с сегодня — не для этого отчёта
    await set_profile(client, tokens, {**OWNER, "supplier_vat_pct": "10", "effective_from": "2026-09-01"})
    assert (await client.get(url, headers=auth(tokens))).json()["pnl"]["owed"] == "360.80"


async def test_tax_by_regime_and_profile_on_period_date(client, tokens):
    await import_reference_costs(client, tokens)
    # до сентября — свой процент, с 01.09.2026 — УСН 6 % с НДС 5 %
    await set_profile(client, tokens, {**OWNER, "effective_from": "2000-01-01"})
    usn = {
        "legal_form": "ip",
        "regime": "usn_income",
        "vat_mode": "special",
        "vat_rate_pct": "5",
        "effective_from": "2026-09-01",
    }
    await set_profile(client, tokens, usn)
    month = (await upload(client, tokens, MONTH))["pnl"]  # август — по старому профилю
    assert month["tax"] == "3977.56" and month["vat"] == "0.00"
    week = (await upload(client, tokens, WEEK))["pnl"]  # сентябрь — по новому
    assert (week["tax"], week["vat"], week["net"]) == ("2357.26", "1964.38", "-5740.71")
    assert week["tax_title"] == "ИП · УСН «Доходы» 6 % · НДС 5 %"
    assert any(line.startswith("НДС 5 % в цене продаж") for line in week["tax_lines"])


async def test_parse_preview_has_pnl(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    r = await client.post(
        "/api/v1/reports/parse", headers=auth(tokens), files={"file": (WEEK, (FIXTURES / WEEK).read_bytes(), XLSX_TYPE)}
    )
    body = r.json()
    assert body["auto_total"] == "10955.83" and body["pnl"]["net"] == "-1419.07"
