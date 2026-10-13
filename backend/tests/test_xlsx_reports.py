"""Разбор XLSX-отчётов Ozon (шаг 2.1): эталонные цифры из docs/BUSINESS_LOGIC.md §3 и синтетические файлы."""
import io
import os
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.core.config import settings
from app.integrations.xlsx import MappingCostProvider, ReportParseError, parse_report
from app.integrations.xlsx.common import extract_period, limit_warnings, norm, parse_number
from app.integrations.xlsx.detector import detect
from app.main import app
from app.services.costs import get_cost_provider, load_cost_csv

FIXTURES = Path(__file__).parent / "fixtures"
VAT = Decimal("1.22")
COSTS = load_cost_csv(settings.COST_PRICES_CSV)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def xlsx(*sheets: tuple[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def owed(cost_sales: Decimal) -> Decimal:
    return (cost_sales * VAT).quantize(Decimal("0.01"))


ACCRUAL_HEADER = ["ID начисления", "Дата начисления", "Тип начисления", "Артикул", "SKU", "Название товара",
                  "Количество", "Сумма итого, руб."]


def accrual_row(type_: str, amount, article: str = "", qty=1) -> list:
    return ["1-1", "07.09.2026", type_, article, "3145353644" if article else "", "Подшипник" if article else "",
            qty, amount]


# ---------- эталоны на реальных файлах ----------


def test_cost_csv_reference():
    assert len(COSTS) == 78
    assert COSTS.cost_of("6001-2RS.C3.SRL.NAC.TH") == Decimal("164")
    assert COSTS.cost_of("6001-2rs.c3.srl.nac.th") is None  # артикул сравнивается посимвольно


def test_accrual_reference_week():
    data = parse_report(fixture("accrual_2026-09-07_13.xlsx"), COSTS)
    assert (data.report_type, data.sheet_name, data.header_row) == ("accrual", "Начисления", 2)
    assert data.period == "07.09.2026-13.09.2026"
    assert (str(data.period_start), str(data.period_end)) == ("2026-09-07", "2026-09-13")
    assert data.auto_total == Decimal("10955.83")
    assert data.revenue_base == Decimal("23712.03")
    assert data.cost_sales == Decimal("10143.36")
    assert owed(data.cost_sales) == Decimal("12374.90")
    assert len(data.by_article) == 16  # позиций с выручкой
    assert data.missing_cost == [] and data.warnings == []
    assert data.by_type["Выручка"] == data.revenue_base
    assert sum(data.by_type.values()) == data.auto_total
    art = data.by_article["6001-2RS.C3.SRL.NAC.TH"]
    assert art.has_cost and art.cost == Decimal("164") and art.total_cost == art.cost * art.qty


def test_realization_reference_month():
    data = parse_report(fixture("realization_2026-08.xlsx"), COSTS)
    assert (data.report_type, data.sheet_name, data.header_row) == ("realization", "Отчет о реализации", 13)
    # период — из «Реализация товаров за период…», а не из «Отчет … от 31.08.2026» строкой выше
    assert data.period == "с 01.08.2026 по 31.08.2026"
    assert (str(data.period_start), str(data.period_end)) == ("2026-08-01", "2026-08-31")
    assert data.by_type == {
        "Итого реализовано (за вычетом возвратов)": Decimal("37461.65"),
        "Всего выплат от партнёров": Decimal("341.70"),
    }
    assert data.revenue_base == Decimal("37461.65")
    assert data.auto_total == Decimal("37803.35")
    assert data.cost_sales == Decimal("14684.89")
    assert owed(data.cost_sales) == Decimal("17915.57")
    assert data.rows_count == 44
    assert data.missing_cost == [] and data.warnings == []


def test_accrual_with_extra_cost_sheet():
    """В файле второй лист «Лист1» со старыми себестоимостями — таблица отчёта не единственная."""
    data = parse_report(fixture("accrual_2026-08-01_09.xlsx"), COSTS)
    assert (data.report_type, data.sheet_name, data.header_row) == ("accrual", "Начисления", 2)
    assert (str(data.period_start), str(data.period_end)) == ("2026-08-01", "2026-08-09")
    assert data.by_type["Выручка"] == data.revenue_base == Decimal("6466.53")
    assert data.cost_sales == Decimal("2553.30")  # совпадает с ручным подсчётом владельца под таблицей
    # под таблицей пометки владельца в колонке «Сумма итого» («Придет на счет 753», «Итого −2362,026») —
    # это не операции Ozon: в сумму не входят, но пользователь о них предупреждён
    assert data.auto_total == sum(data.by_type.values()) == Decimal("742.83")
    assert data.warnings == [  # «#N/A» в колонке без заголовка не разбирается
        "Строк без «Типа начисления» не учтено: 3 (на сумму -1609.03 ₽). Это не операции Ozon — похоже на "
        "итоги или пометки, добавленные в файл вручную."
    ]


# ---------- синтетические файлы ----------


def test_table_not_on_first_sheet():
    content = xlsx(
        ("Инструкция", [["Как читать отчёт"], ["Артикул", "Цена"], ["6001-2RS.C3.SRL.NAC.TH", 164]]),
        ("Данные", [["Период: 01.09.2026-07.09.2026"], ACCRUAL_HEADER,
                    accrual_row("Выручка", 500, "6001-2RS.C3.SRL.NAC.TH", 2), accrual_row("Логистика", -74)]),
    )
    data = parse_report(content, COSTS)
    assert (data.sheet_name, data.header_row) == ("Данные", 2)
    assert data.auto_total == Decimal("426") and data.revenue_base == Decimal("500")
    assert data.cost_sales == Decimal("328")  # 164 × 2


def test_extra_rows_above_realization_table():
    """Шапка из 15 строк, период — второй строкой с датой; строка нумерации начинается с первой колонки."""
    junk = [[], ["Какая-то служебная строка"], [None, None, "ООО «Ромашка»"]] * 4
    rows = [
        *junk,
        [None, None, "Отчет о реализации № 1 от 31.08.2026"],
        [None, None, "Реализация товаров за период с 01.08.2026 по 31.08.2026"],
        [],
        ["№ п/п", "Название товара", "Артикул", "SKU", "Реализовано", None, None],
        [None, None, None, None, "Реализовано на сумму, руб.", "Выплаты по механикам лояльности партнёров, руб.",
         "Кол-во"],
        [1, 2, 3, 4, 5, 6, 7],  # нумерация столбцов — не строка товара
        [1, "Подшипник", "6001-2RS.C3.SRL.NAC.TH", 3145353644, 1000, 10, 2],
        [2, "Подшипник", "6000.CM.NAC.JP", 3145353000, 300.5, 3, 1],
        [None, "Итого реализовано (за вычетом возвратов) (руб.):", 1300.5],
        [None, "Всего выплат от партнёров (руб.):", 13],
    ]
    data = parse_report(xlsx(("Отчет", rows)), COSTS)
    assert data.report_type == "realization" and data.header_row == 16
    assert data.period == "с 01.08.2026 по 31.08.2026"
    assert data.rows_count == 2
    assert data.auto_total == Decimal("1313.50") and data.revenue_base == Decimal("1300.50")
    assert data.cost_sales == Decimal("164") * 2 + Decimal("142.49")


def test_headers_with_line_breaks_and_extra_spaces():
    header = ["ID\nначисления", "  Тип\nначисления ", "Артикул\n", " SKU ", "Название товара",
              "Коли-\nчество", "Количество", "Сумма   итого,\nруб. "]
    rows = [header, ["1", "Выручка", "6001-2RS.C3.SRL.NAC.TH", "1", "Подшипник", None, 3, "1 500,00"],
            ["2", "Вознаграждение\nза продажу", "6001-2RS.C3.SRL.NAC.TH", "1", "Подшипник", None, 3, "-600"]]
    data = parse_report(xlsx(("Начисления", rows)), COSTS)
    assert data.header_row == 1
    assert data.auto_total == Decimal("900") and data.revenue_base == Decimal("1500")
    assert data.by_article["6001-2RS.C3.SRL.NAC.TH"].qty == 3
    assert data.cost_sales == Decimal("492")  # 164 × 3
    assert "Вознаграждение\nза продажу" in data.by_type  # тип — как в отчёте

    real = [
        ["№\nп/п", "Название  товара", " Артикул ", "Реализовано"],
        [None, None, None, "Реализовано\nна сумму,  руб.", "Кол-\nво", "Кол-во"],
        [1, "Подшипник", "6001-2RS.C3.SRL.NAC.TH", "1 000,00", None, 2],
        [None, "Итого реализовано (за вычетом возвратов) (руб.):", "1 000,00"],
        [None, "Всего выплат от партнёров (руб.):", "0"],
    ]
    data = parse_report(xlsx(("Р", real)), COSTS)
    assert data.report_type == "realization"
    assert data.cost_sales == Decimal("328") and data.auto_total == Decimal("1000")


def test_unknown_article_goes_to_missing_cost():
    rows = [ACCRUAL_HEADER,
            accrual_row("Выручка", 700, "UNKNOWN-1", 2), accrual_row("Выручка", 350, "UNKNOWN-1", 1),
            accrual_row("Выручка", 500, "6001-2RS.C3.SRL.NAC.TH", 1), accrual_row("Выручка", 100, "", 1)]
    data = parse_report(xlsx(("Начисления", rows)), COSTS)
    assert [m.model_dump() for m in data.missing_cost] == [
        {"article": "UNKNOWN-1", "sku": "3145353644", "name": "Подшипник", "qty": 3}
    ]
    assert data.cost_sales == Decimal("164")  # неизвестный — не 0 × qty, а вне суммы и в списке
    unknown = data.by_article["UNKNOWN-1"]
    assert (unknown.has_cost, unknown.cost, unknown.total_cost, unknown.qty) == (False, None, None, 3)
    assert unknown.revenue == Decimal("1050")
    assert any("Не найдена себестоимость для 1 артикулов (3 шт.)" in w for w in data.warnings)
    assert any("без артикула: 1" in w for w in data.warnings)


def test_realization_mock_from_reference_selftest():
    """Тот же разбор, что и во встроенных самотестах reference/ozon_calculator.html, — числа строками."""
    rows = [
        ["Отчет о реализации товаров"],
        ["Реализация товаров за период с 01.08.2026 по 31.08.2026"],
        ["№ п/п", "Название товара", "Артикул", "SKU", "Штрих-код", "Реализовано"],
        ["", "", "", "", "", "Реализовано на сумму, руб.", "Выплаты по механикам", "Кол-во"],
        ["", "1", "2", "3", "4", "5", "6", "7"],
        ["1", "Подшипник", "6001-2RS.C3.SRL.NAC.TH", "123", "456", "500,00", "5,00", "2"],
        ["2", "Неизвестный", "UNKNOWN_ART", "789", "000", "200 ₽", "2", "1"],
        ["", "Итого реализовано (за вычетом возвратов) (руб.):", "700,00"],
        ["", "Всего выплат от партнёров (руб.):", "7"],
    ]
    data = parse_report(xlsx(("Sheet1", rows)), COSTS)
    assert data.period == "с 01.08.2026 по 31.08.2026"
    assert data.rows_count == 2
    assert data.auto_total == Decimal("707") and data.revenue_base == Decimal("700")
    assert data.cost_sales == Decimal("328")
    assert data.by_article["UNKNOWN_ART"].revenue == Decimal("200")
    assert [m.article for m in data.missing_cost] == ["UNKNOWN_ART"]


REALIZATION_TABLE = [
    ["№ п/п", "Название товара", "Артикул", "Реализовано"],
    [None, None, None, "Реализовано на сумму, руб.", "Кол-во"],
    [1, "Подшипник", "6001-2RS.C3.SRL.NAC.TH", 1000, 2],
    [2, "Подшипник", "6000.CM.NAC.JP", 500, 1],
]


def test_realization_without_payments_total_warns():
    rows = [*REALIZATION_TABLE, [None, "Итого реализовано (за вычетом возвратов) (руб.):", 1400]]
    data = parse_report(xlsx(("Р", rows)), COSTS)
    assert data.auto_total == Decimal("1400")
    assert any("В отчёте о реализации не найден показатель «Всего выплат от партнёров»" in w for w in data.warnings)


def test_realization_without_realized_total_uses_rows():
    rows = [*REALIZATION_TABLE, [None, "Всего выплат от партнёров (руб.):", 15]]
    data = parse_report(xlsx(("Р", rows)), COSTS)
    assert data.revenue_base == Decimal("1500") and data.auto_total == Decimal("1515")
    assert any("«Итого реализовано (за вычетом возвратов)»" in w for w in data.warnings)


def test_showcase_realization_report_is_not_detected():
    rows = [["Показной отчет о реализации"], *REALIZATION_TABLE]
    with pytest.raises(ReportParseError, match="Не удалось определить тип отчёта Ozon"):
        parse_report(xlsx(("Р", rows)), COSTS)


def test_garbage_files():
    with pytest.raises(ReportParseError, match="Не удалось открыть файл Excel"):
        parse_report(os.urandom(2048), COSTS)
    with pytest.raises(ReportParseError, match="Не удалось открыть файл Excel"):
        parse_report(b"PK\x03\x04 not really a zip", COSTS)
    with pytest.raises(ReportParseError, match=r"\.xls"):
        parse_report(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512, COSTS)
    content = xlsx(("Продажи", [["Дата", "Сумма"], ["01.09.2026", 100]]), ("Заметки", [["привет"]]))
    with pytest.raises(ReportParseError, match="Не удалось определить тип отчёта Ozon.*Листы в файле: Продажи, Заметки"):  # noqa: E501
        parse_report(content, COSTS)


def test_header_without_rows():
    with pytest.raises(ReportParseError, match="строк с данными нет"):
        parse_report(xlsx(("Начисления", [["Период: 01.09.2026-07.09.2026"], ACCRUAL_HEADER])), COSTS)


def test_unreadable_numbers_are_warnings_not_silent_zero():
    rows = [ACCRUAL_HEADER, accrual_row("Выручка", 500, "6001-2RS.C3.SRL.NAC.TH", "два"),
            accrual_row("Логистика", "#N/A"), accrual_row("Логистика", "-74,5")]
    data = parse_report(xlsx(("Н", rows)), COSTS)
    assert data.auto_total == Decimal("425.50")
    assert data.warnings[:2] == [
        "Не удалось разобрать число «два» в поле «Количество» (строка 2) — значение не учтено.",
        "Не удалось разобрать число «#N/A» в поле «Сумма итого» (строка 3) — значение не учтено.",
    ]


def test_many_unreadable_numbers_are_collapsed():
    rows = [ACCRUAL_HEADER, accrual_row("Выручка", 1, "6001-2RS.C3.SRL.NAC.TH"),
            *[accrual_row("Логистика", "x") for _ in range(25)]]
    data = parse_report(xlsx(("Н", rows)), COSTS)
    assert len(data.warnings) == 21 and data.warnings[-1] == "…и ещё нечитаемых чисел: 5."


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1 234,56", "1234.56"), ("1234.56", "1234.56"), ("1 234 ₽", "1234"), ("-304.01", "-304.01"),
        ("−304,01", "-304.01"), ("1 234,5", "1234.5"), ("1 234,56 руб.", "1234.56"), ("(15,00)", "-15.00"),
        ("1,234.56", "1234.56"), ("1.234,56", "1234.56"), ("+7", "7"), (482.66, "482.66"), (-65, "-65"),
        ("", None), ("   ", None), (None, None),
    ],
)
def test_parse_number(raw, expected):
    warnings: list[str] = []
    result = parse_number(raw, warnings, "Поле")
    assert result == (Decimal(expected) if expected is not None else None)
    assert warnings == []


@pytest.mark.parametrize("raw", ["abc", "#N/A", "12abc", "1-2", True, float("nan")])
def test_parse_number_unreadable(raw):
    warnings: list[str] = []
    assert parse_number(raw, warnings, "Сумма итого") is None
    assert len(warnings) == 1 and "в поле «Сумма итого»" in warnings[0]


def test_norm_and_period_helpers():
    assert norm("  Сумма итого,\n\tруб. ") == "сумма итого, руб."
    assert norm(None) == "" and norm("Партнёров") == "партнеров"
    assert extract_period([["Период: 07.09.2026-13.09.2026"]])[0] == "07.09.2026-13.09.2026"
    assert extract_period([["нет дат"]]) == ("", None, None)
    assert limit_warnings(["a", "b"]) == ["a", "b"]


def test_detector_filename_is_only_a_tiebreaker():
    sheets = {"A": [["Тип начисления", "Сумма итого"]], "B": [["Реализовано", "Артикул"]]}
    assert detect(sheets).report_type == "accrual"  # равная уверенность — первый лист
    assert detect(sheets, "Отчет о реализации 08.2026.xlsx").report_type == "realization"
    # бонусные признаки весомее имени файла
    sheets["B"] = [["Реализовано", "Артикул", "Кол-во", "SKU"]]
    assert detect(sheets, "начисления.xlsx").report_type == "realization"
    assert detect({"A": [["Артикул", "Цена"]]}) is None


def test_cost_csv_missing_file_and_bad_lines(tmp_path):
    assert len(load_cost_csv(tmp_path / "нет.csv")) == 0
    path = tmp_path / "costs.csv"
    path.write_text("offer_id,cost_price\nA,10,5\nB,abc\n,5\nC,-1\nD,7\n", encoding="utf-8")
    costs = load_cost_csv(path)
    assert (costs.cost_of("A"), costs.cost_of("B"), costs.cost_of("C"), costs.cost_of("D")) == (
        Decimal("10"), None, None, Decimal("7")
    )


# ---------- API ----------


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload(name: str, content: bytes) -> dict:
    return {"file": (name, content, XLSX_TYPE)}


async def test_api_parse_requires_auth(client):
    r = await client.post("/api/v1/reports/parse", files=upload("a.xlsx", fixture("accrual_2026-09-07_13.xlsx")))
    assert r.status_code == 401


async def test_api_parse_reference_files(client, tokens):
    r = await client.post("/api/v1/reports/parse", headers=auth(tokens),
                          files=upload("accrual.xlsx", fixture("accrual_2026-09-07_13.xlsx")))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_type"] == "accrual" and body["period_start"] == "2026-09-07"
    assert (body["auto_total"], body["revenue_base"], body["cost_sales"]) == ("10955.83", "23712.03", "10143.36")
    assert body["missing_cost"] == [] and len(body["by_article"]) == 16

    r = await client.post("/api/v1/reports/parse", headers=auth(tokens),
                          files=upload("realization.xlsx", fixture("realization_2026-08.xlsx")))
    assert r.status_code == 200, r.text
    assert (r.json()["auto_total"], r.json()["rows_count"]) == ("37803.35", 44)


async def test_api_cost_provider_is_replaceable(client, tokens):
    """Подмена источника себестоимости (шаг 2.3) — без правки парсеров."""
    app.dependency_overrides[get_cost_provider] = lambda: MappingCostProvider({})
    r = await client.post("/api/v1/reports/parse", headers=auth(tokens),
                          files=upload("a.xlsx", fixture("accrual_2026-09-07_13.xlsx")))
    body = r.json()
    assert body["cost_sales"] == "0.00" and len(body["missing_cost"]) == 16
    assert any(w.startswith("Не найдена себестоимость для 16 артикулов") for w in body["warnings"])


async def test_api_parse_errors(client, tokens, monkeypatch):
    post = lambda **kw: client.post("/api/v1/reports/parse", headers=auth(tokens), **kw)  # noqa: E731

    r = await post(files=upload("junk.xlsx", b"definitely not excel"))
    assert r.status_code == 400 and r.json()["code"] == 400
    assert r.json()["detail"].startswith("Не удалось открыть файл Excel")

    r = await post(files=upload("other.xlsx", xlsx(("Лист1", [["Артикул", "Цена"]]))))
    assert r.status_code == 400 and r.json()["detail"].startswith("Не удалось определить тип отчёта Ozon")

    r = await post(files=upload("empty.xlsx", b""))
    assert (r.status_code, r.json()["detail"]) == (400, "Файл пустой.")

    r = await post()
    assert (r.status_code, r.json()["detail"]) == (400, "Файл: обязательное поле")

    monkeypatch.setattr(settings, "REPORT_MAX_SIZE_MB", 0)
    r = await post(files=upload("big.xlsx", b"x" * 10))
    assert r.status_code == 413 and "МБ" in r.json()["detail"]
