"""Налоговый профиль через API (шаг 2.5): история, проверки, справочник для мастера, предпросмотр."""

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "accrual_2026-09-07_13.xlsx"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
USN_IP = {"legal_form": "ip", "regime": "usn_income"}


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def put(client, tokens, body):
    return await client.put("/api/v1/tax-profile", json=body, headers=auth(tokens))


async def preview(client, tokens, body):
    r = await client.post("/api/v1/tax-profile/preview", json=body, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_no_profile_means_tax_not_set(client, tokens):
    assert (await client.get("/api/v1/tax-profile")).status_code == 401
    state = (await client.get("/api/v1/tax-profile", headers=auth(tokens))).json()
    assert state == {"current": None, "history": [], "created": None}  # не 0 и не чужие 20 %


async def test_options_for_wizard(client, tokens):
    o = (await client.get("/api/v1/tax-profile/options", headers=auth(tokens))).json()
    assert o["forms"] == {"ip": "ИП", "ooo": "ООО", "self_employed": "Самозанятый"}
    regimes = {r["key"]: r for r in o["regimes"]}
    assert len(regimes) == 9
    assert regimes["usn_income"]["default_rates"] == {"ip": "6", "ooo": "6"}
    assert regimes["osn"]["default_rates"] == {"ip": "13", "ooo": "25"}
    assert regimes["usn_income_expense"]["min_rate"] == "1" and regimes["osn"]["vat_modes"] == ["general"]
    assert (
        regimes["npd"]["forms"] == ["self_employed", "ip"]
        and "собственного производства" in regimes["npd"]["restriction"]
    )
    assert any(line.startswith("Налог = доход × 6 %.") for line in regimes["usn_income"]["how"])
    assert regimes["custom"]["how"][0].endswith("× ваш процент; при убытке — 0.")
    assert o["vat_rates"] == {"none": [0], "special": [5, 7], "general": [22, 10, 0]}
    assert o["supplier_vat_rates"] == [22, 20, 10, 7, 5, 0] and o["supplier_default"] == 22
    assert o["limits"]["usn_vat_free_income"] == 20_000_000


async def test_save_profile_with_history(client, tokens):
    r = await put(client, tokens, USN_IP)  # ставка не указана — стандартная
    state = r.json()
    assert r.status_code == 200 and state["created"] is True
    current = state["current"]
    assert (current["regime"], current["rate_pct"], current["vat_mode"], current["effective_from"]) == (
        "usn_income",
        "6.00",
        "none",
        "2000-01-01",  # первый профиль — с начала учёта
    )
    assert current["title"] == "ИП · УСН «Доходы» 6 % · без НДС"
    assert (await put(client, tokens, USN_IP)).json()["created"] is False  # такой же — новой записи нет

    # с 2026 года — ООО на УСН «доходы минус расходы» с НДС 5 %: прежний профиль остаётся в истории
    later = {
        "legal_form": "ooo",
        "regime": "usn_income_expense",
        "vat_mode": "special",
        "vat_rate_pct": "5",
        "effective_from": "2026-01-01",
    }
    state = (await put(client, tokens, later)).json()
    assert state["current"]["title"] == "ООО · УСН «Доходы минус расходы» 15 % · НДС 5 %"
    assert [h["effective_from"] for h in state["history"]] == ["2026-01-01", "2000-01-01"]  # новые сверху
    assert state["history"][1]["regime"] == "usn_income"

    custom = {"regime": "custom", "rate_pct": "20", "custom_base": "profit", "effective_from": "2026-02-01"}
    state = (await put(client, tokens, custom)).json()
    assert state["current"]["title"] == "Свой процент: 20 % от прибыли" and state["current"]["legal_form"] is None
    patent = {"legal_form": "ip", "regime": "psn", "patent_cost_year": "36500", "effective_from": "2026-03-01"}
    assert (await put(client, tokens, patent)).json()["current"][
        "title"
    ] == "ИП · ПСН — патент, 36 500,00 ₽ в год · без НДС"


@pytest.mark.parametrize(
    "body,text",
    [
        ({"regime": "usn_income"}, "Укажите, кто вы: ИП, ООО или самозанятый."),
        ({"legal_form": "ooo", "regime": "npd"}, "Режим «НПД — самозанятый» недоступен для формы «ООО»."),
        ({"legal_form": "ooo", "regime": "psn"}, "Режим «ПСН — патент» недоступен для формы «ООО»."),
        (
            {"legal_form": "ip", "regime": "osn", "vat_mode": "special", "vat_rate_pct": "5"},
            "При режиме «ОСН — общая система» такой вариант НДС невозможен.",
        ),
        ({**USN_IP, "vat_mode": "special", "vat_rate_pct": "10"}, "Ставка НДС для этого варианта — 5 %, 7 %."),
        ({**USN_IP, "vat_mode": "general", "vat_rate_pct": "5"}, "Ставка НДС для этого варианта — 22 %, 10 %, 0 %."),
        ({**USN_IP, "supplier_vat_pct": "15"}, "НДС в цене поставщика — 22 %, 20 %, 10 %, 7 %, 5 % или 0 % (без НДС)."),
        ({"regime": "custom", "custom_base": "profit"}, "Укажите свой процент налога — больше 0."),
        ({"regime": "custom", "rate_pct": "20"}, "Укажите, от чего считать свой процент: от прибыли или от дохода."),
        ({"legal_form": "ip", "regime": "psn"}, "Укажите стоимость патента за год."),
        ({"legal_form": "ip", "regime": "xyz"}, "Система налогообложения: недопустимое значение"),
        ({**USN_IP, "rate_pct": "120"}, "Ставка налога: не больше 100"),
    ],
)
async def test_validation(client, tokens, body, text):
    r = await put(client, tokens, body)
    assert (r.status_code, r.json()["detail"]) == (400, text)
    r = await client.post("/api/v1/tax-profile/preview", json=body, headers=auth(tokens))
    assert (r.status_code, r.json()["detail"]) == (400, text)


async def test_preview_explains_and_counts(client, tokens):
    p = await preview(client, tokens, {**USN_IP, "rate_pct": "6"})
    assert p["title"] == "ИП · УСН «Доходы» 6 % · без НДС"
    assert any(line.startswith("Налог = доход × 6 %") for line in p["how"])
    assert p["example"]["tax"] == "600.00" and p["example"]["income"] == "10000.00"  # 6 % от цены продажи
    assert p["example"]["lines"][0] == "Доход: 10 000,00 ₽ — цена продажи покупателю"
    assert p["report_example"] is None  # отчётов ещё нет
    # «доходы минус расходы»: 10 000 − 4 000 − 3 000 × 1,22 = 2 340 → 15 % = 351
    p = await preview(client, tokens, {"legal_form": "ooo", "regime": "usn_income_expense"})
    assert (p["example"]["cost_with_vat"], p["example"]["tax"]) == ("3660.00", "351.00")
    # ничего не сохранилось
    assert (await client.get("/api/v1/tax-profile", headers=auth(tokens))).json()["current"] is None


async def test_preview_on_latest_report(client, tokens):
    r = await client.post(
        "/api/v1/reports/upload", headers=auth(tokens), files={"file": ("a.xlsx", FIXTURE.read_bytes(), XLSX_TYPE)}
    )
    assert r.status_code == 201
    p = await preview(client, tokens, USN_IP)
    report = p["report_example"]
    assert report["title"] == "Отчёт по начислениям 07.09.2026-13.09.2026"
    # доход — полная цена продаж (41 252,00), а не выплата Ozon (10 955,83)
    assert (report["income"], report["tax"]) == ("41252.00", "2475.12")
    assert report["warnings"] == []  # на «доходах» себестоимость налог не меняет — не о чем предупреждать
    # «свой процент от прибыли» 20 %: (10 955,83 − себестоимость с НДС) × 20 %, при убытке — 0
    p = await preview(client, tokens, {"regime": "custom", "rate_pct": "20", "custom_base": "profit"})
    assert p["report_example"]["tax"] == "2191.17"  # себестоимости нет: 10 955,83 × 20 %
    assert "Себестоимость не найдена для 16 арт. — расходы занижены." in p["report_example"]["warnings"]


async def test_profiles_are_private(client, tokens):
    await put(client, tokens, USN_IP)
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (
        await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})
    ).json()
    assert (await client.get("/api/v1/tax-profile", headers=auth(other))).json()["current"] is None
