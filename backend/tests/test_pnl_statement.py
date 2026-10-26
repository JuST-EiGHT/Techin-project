"""P&L строками с прочими расходами бизнеса (шаг 10.22): строки Ozon по статьям начислений, расходы бизнеса из «Операций»
(статьи «в P&L», подстатьи, возвраты, переводы, без статьи, вне P&L), налог с расходами по режиму; без расходов бизнеса —
ровно P&L 3.1 (эталоны не меняются)."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.engine import pnl_statement as eng
from app.engine.tax import Profile, TaxInput, compute_tax
from app.models.pnl_snapshot import PnLSnapshot
from app.services.pnl_cache import cache
from tests.test_plan_calendar import (  # noqa: F401 — фикстура «сегодня»
    call,
    fixed_today,
    setup,
)
from tests.test_pnl_api import pnl
from tests.test_report_pnl import (
    OWNER,
    WEEK,
    auth,
    import_reference_costs,
    set_profile,
    upload,
)

D = Decimal


def rows(body) -> dict[str, dict]:
    return {r["key"]: r for r in body["statement"]["rows"]}


# ---------- engine ----------


def test_ozon_lines_and_totals():
    by_type = {
        "Выручка": D(1000), "Возврат выручки": D(-100), "Баллы за скидки": D(50), "Вознаграждение за продажу": D(-150),
        "Возврат вознаграждения": D(15), "Логистика": D(-80), "Размещение на складе": D(-20), "Оплата за клик": D(-30),
        "Эквайринг": D(-10),
    }
    s = eng.build(by_type, D(1000), D(675), D(700), D(400), "Себестоимость с НДС",
                  [eng.Category("a", "Аренда", D(100))], D(30), D(0), "УСН 15 %")
    got = {r.key: r.amount for r in s.rows}
    assert got == {
        "revenue": D(1000), "returns": D(-100), "adjustments": D(50), "commission": D(-135), "logistics": D(-80),
        "storage": D(-20), "ads": D(-30), "other": D(-10), "manual": D(25), "ozon_total": D(700), "cost": D(-400),
        "profit_ozon": D(300), "business": D(-100), "profit_before_tax": D(200), "tax": D(-30), "net": D(170),
    }
    assert sum(r.amount for r in s.rows if r.kind == "line" and r.key not in ("cost", "business", "tax")) == D(700)
    assert (s.business, s.profit, s.margin) == (D(100), D(200), D("17.00"))
    assert [n for n, _ in next(r for r in s.rows if r.key == "commission").types] == [
        "Вознаграждение за продажу", "Возврат вознаграждения"]
    # налог не задан — чистой прибыли нет; без расходов бизнеса — строк бизнеса нет
    s = eng.build({"Выручка": D(1000)}, D(1000), D(1000), D(1000), D(400), "Себестоимость", None, None, None, None)
    assert [r.key for r in s.rows] == ["revenue", "ozon_total", "cost", "profit_ozon", "tax", "net"]
    assert s.net is None and s.margin is None and s.business is None
    # НДС к уплате — своей строкой
    s = eng.build({"Выручка": D(1000)}, D(1000), D(1000), D(1000), D(400), "С", None, D(60), D(100), "ОСН")
    assert [(r.key, r.amount) for r in s.rows][-3:] == [("tax", D(-60)), ("vat", D(-100)), ("net", D(440))]


def test_tax_with_business_expenses_by_regime():
    """Расходы бизнеса уменьшают базу у режимов «от прибыли», у «от дохода» — нет (только строка-пояснение)."""
    day = date(2026, 9, 7)
    d = TaxInput(income=D(10000), ozon_expenses=D(3000), cost_sales=D(2000), cost_with_vat=D(2440), payout=D(7000),
                 revenue_base=D(10000))
    with_b = replace(d, business_expenses=D(1500))
    usn15 = Profile("usn_income_expense", "ip")
    assert (compute_tax(usn15, d, day).tax, compute_tax(usn15, with_b, day).tax) == (D("684.00"), D("459.00"))  # × 15 %
    usn6 = Profile("usn_income", "ip")
    assert compute_tax(usn6, d, day).tax == compute_tax(usn6, with_b, day).tax == D("600.00")
    assert "налог не уменьшают" in compute_tax(usn6, with_b, day).lines[-2]
    own = Profile("custom", "ip", rate_pct=D(20), custom_base="profit")  # (пришло − себестоимость с НДС − расходы) × 20 %
    assert (compute_tax(own, d, day).tax, compute_tax(own, with_b, day).tax) == (D("912.00"), D("612.00"))


# ---------- API ----------


async def test_without_business_matches_pnl(client, tokens):
    """Планирование выключено: строки P&L — те же цифры, что P&L 3.1 (эталон недели), строки расходов бизнеса нет."""
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    body = await pnl(client, tokens)
    p, st, r = body["pnl"], body["statement"], rows(body)
    assert body["business"] is None and "business" not in r and "profit_before_tax" not in r
    assert list(r) == ["revenue", "returns", "adjustments", "commission", "logistics", "storage", "ads", "other",
                       "ozon_total", "cost", "profit_ozon", "tax", "net"]
    ozon = sum(D(x["amount"]) for x in body["statement"]["rows"][:8])
    assert ozon == D(body["auto_total"]) == D(r["ozon_total"]["amount"]) == D("10955.83")
    assert (r["revenue"]["amount"], r["returns"]["amount"], r["adjustments"]["amount"]) == (
        "23712.03", "-2588.18", "20128.15")
    assert r["cost"] == {**r["cost"], "title": "Себестоимость с НДС", "amount": "-12374.90"}
    assert st["profit_ozon"] == st["profit_before_tax"] == p["profit_before_tax"] == "-1419.07"
    assert (st["tax"], st["net"], st["margin"]) == (p["tax"], p["net"], p["margin"]) == ("0.00", "-1419.07", "-5.98")
    assert r["net"]["pct"] == "-5.98" and r["revenue"]["pct"] == "100.00"


async def test_business_expenses_and_tax(client, tokens):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    base = await pnl(client, tokens, manual_total="50000")  # прибыль от Ozon в плюс — налог есть
    assert (base["statement"]["net"], base["pnl"]["net"], base["pnl"]["tax"]) == ("30100.08", "30100.08", "7525.02")

    cats, accounts = await setup(client, tokens)
    acc = accounts["Расчётный счёт"]
    # включили планирование, расходов ещё нет — строка есть, ноль, цифры P&L те же
    body = await pnl(client, tokens, manual_total="50000")
    assert body["business"]["total"] == "0.00" and rows(body)["business"]["amount"] == "0.00"
    assert body["statement"]["net"] == "30100.08"

    marketing = (await call(client, tokens, "POST", "/categories", 201, json={"name": "Маркетинг", "kind": "expense"}))["id"]
    vk = (await call(client, tokens, "POST", "/categories", 201, json={
        "name": "Реклама VK", "kind": "expense", "parent_id": marketing}))["id"]
    cash = (await call(client, tokens, "POST", "/accounts", 201, json={"name": "Наличные", "kind": "cash"}))["id"]
    ops = [
        {"type": "expense", "amount": "30000", "day": "2026-09-08", "category_id": cats["rent"]},
        {"type": "income", "amount": "2000", "day": "2026-09-10", "category_id": cats["rent"]},  # вернули часть аренды
        {"type": "expense", "amount": "4000", "day": "2026-09-09", "category_id": vk},
        {"type": "expense", "amount": "50000", "day": "2026-09-09", "category_id": cats["purchase"]},  # в себестоимости
        {"type": "expense", "amount": "5000", "day": "2026-09-11", "category_id": cats["taxes"]},  # строкой налога
        {"type": "expense", "amount": "1000", "day": "2026-09-12"},  # без статьи
        {"type": "expense", "amount": "10000", "day": "2026-09-20", "category_id": cats["rent"]},  # вне периода
        {"type": "income", "amount": "7000", "day": "2026-09-08", "category_id": cats["marketplace"]},
        {"type": "transfer", "amount": "3000", "day": "2026-09-08", "to_account_id": cash},
    ]
    for op in ops:
        await call(client, tokens, "POST", "/operations", 201, json={"account_id": acc, **op})

    body = await pnl(client, tokens, manual_total="50000")  # без кэша: операции видны сразу
    b, st, r = body["business"], body["statement"], rows(body)
    assert b["total"] == "32000.00" and b["operations"] == 3
    assert [(c["name"], c["amount"], [(x["name"], x["amount"]) for x in c["children"]]) for c in b["categories"]] == [
        ("Аренда", "28000.00", []), ("Маркетинг", "4000.00", [("Реклама VK", "4000.00")])]
    assert [(x["name"], x["amount"]) for x in b["excluded"]] == [("Закупка товара", "50000.00"), ("Налоги и взносы", "5000.00")]
    assert b["uncategorized"] == {"count": 1, "amount": "1000.00"}
    assert r["business"]["amount"] == "-32000.00" and [c["name"] for c in r["business"]["categories"]] == [
        "Аренда", "Маркетинг"]
    # прибыль от Ozon не меняется; с расходами бизнеса база налога меньше: (50 000 − 12 374,90 − 32 000) × 20 %
    assert (st["profit_ozon"], st["business"], st["profit_before_tax"]) == ("37625.10", "32000.00", "5625.10")
    assert (st["tax"], st["net"], st["margin"]) == ("1125.02", "4500.08", "18.98")
    assert body["pnl"]["net"] == "30100.08"  # P&L Ozon — как был
    assert any("расходы бизнеса 32 000,00" in line for line in st["tax_lines"])

    # УСН «доходы»: расходы бизнеса налог не уменьшают
    sept = {"legal_form": "ip", "effective_from": "2026-09-01"}  # профиль на дату периода
    await set_profile(client, tokens, {**sept, "regime": "usn_income"})
    body = await pnl(client, tokens, manual_total="50000")
    st = body["statement"]
    assert st["tax"] == body["pnl"]["tax"] and any("налог не уменьшают" in line for line in st["tax_lines"])
    assert D(st["net"]) == D(st["profit_before_tax"]) - D(st["tax"]) - D(st["vat"] or 0)
    # УСН «доходы минус расходы»: расходы бизнеса — в расходах базы (здесь убыток — минимальный налог 1 % от дохода)
    await set_profile(client, tokens, {**sept, "regime": "usn_income_expense"})
    body = await pnl(client, tokens, manual_total="50000")
    assert body["statement"]["tax"] == body["pnl"]["tax"] == "412.52"
    assert any("прочие расходы бизнеса 32 000,00" in line for line in body["statement"]["tax_lines"])

    # статью убрали из P&L — она в «не вошло»
    await call(client, tokens, "PATCH", f"/categories/{cats['rent']}", json={"in_pnl": False})
    body = await pnl(client, tokens, manual_total="50000")
    assert body["business"]["total"] == "4000.00"
    assert ("Аренда", "28000.00") in [(x["name"], x["amount"]) for x in body["business"]["excluded"]]


async def test_old_snapshot_without_tax_inputs_is_recomputed(client, tokens, sessions):
    """Снимок закрытого месяца до шага 10.22 — без сумм для налога: пересчитывается, строки P&L есть."""
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    first = await pnl(client, tokens, date_from="2026-09-01", date_to="2026-09-30")
    async with sessions() as db:
        snap = (await db.execute(select(PnLSnapshot))).scalar_one()
        assert "tax_input" in snap.data_json
        snap.data_json = {k: v for k, v in snap.data_json.items() if k != "tax_input"}
        await db.commit()
    cache.clear_memory()
    body = await pnl(client, tokens, date_from="2026-09-01", date_to="2026-09-30")
    assert body["statement"]["net"] == first["statement"]["net"] == first["pnl"]["net"]
    async with sessions() as db:
        assert "tax_input" in (await db.execute(select(PnLSnapshot))).scalar_one().data_json


# ---------- «Финансы → Обзор» ----------


async def test_finance_overview(client, tokens, sessions):
    await import_reference_costs(client, tokens)
    await set_profile(client, tokens, OWNER)
    await upload(client, tokens, WEEK)
    url = "/api/v1/finance/overview"
    week = {"date_from": "2026-09-07", "date_to": "2026-09-13"}
    r = await client.get(url, params=week, headers=auth(tokens))
    body = r.json()
    f = body["figures"]
    # без планирования: прибыль — чистая P&L 3.1, расходы = выручка − прибыль, прошлой недели нет — не 0
    assert (body["profit_kind"], body["tax_set"], body["business"], body["has_data"]) == ("net", True, None, True)
    assert (f["revenue"]["value"], f["profit"]["value"], f["expenses"]["value"], f["margin"]["value"]) == (
        "23712.03", "-1419.07", "25131.10", "-5.98")
    assert f["revenue"]["previous"] is None and f["revenue"]["change_pct"] is None
    assert body["previous"] == {"date_from": "2026-08-31", "date_to": "2026-09-06", "has_data": False}
    assert sum(D(i["amount"]) for i in body["structure"]) == D("25131.10")
    assert [(i["title"], i["share"]) for i in body["structure"][:2]] == [("Комиссии Ozon", "81.77"), ("Себестоимость с НДС", "52.19")]
    adj = next(i for i in body["structure"] if i["key"] == "adjustments")  # доплаты Ozon — в минус, своей строкой
    assert (adj["title"], adj["amount"]) == ("Корректировки и доплаты Ozon", "-20128.15")

    cats, accounts = await setup(client, tokens)
    await call(client, tokens, "POST", "/operations", 201, json={
        "account_id": accounts["Расчётный счёт"], "type": "expense", "amount": "30000", "day": "2026-09-08",
        "category_id": cats["rent"]})
    body = (await client.get(url, params=week, headers=auth(tokens))).json()
    f = body["figures"]
    assert body["business"] == {"total": "30000.00", "operations": 1, "uncategorized": {"count": 0, "amount": "0.00"}}
    assert (f["profit"]["value"], f["expenses"]["value"], body["profit_ozon"]) == ("-31419.07", "55131.10", "-1419.07")
    rent = next(i for i in body["structure"] if i["kind"] == "business")
    assert (rent["title"], rent["amount"], rent["category_id"]) == ("Аренда", "30000.00", cats["rent"])
    assert sum(D(i["amount"]) for i in body["structure"]) == D("55131.10")
    assert (await client.get(url, params={"date_from": "2026-09-13", "date_to": "2026-09-01"},
                             headers=auth(tokens))).status_code == 400


async def test_finance_problems_filter(client, tokens, sessions):
    from datetime import datetime, timezone

    from app.engine import anomaly_detector as det
    from app.models.anomaly import Anomaly
    from tests.test_pnl_api import user_id

    uid = await user_id(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        for type_, details in [(det.UNCATEGORIZED, {"oldest_day": "2026-09-01"}), (det.ACCRUAL_GAPS, {"count": 2}),
                               (det.SYNC_PROBLEM, {"titles": ["Остатки"]})]:
            db.add(Anomaly(user_id=uid, type=type_, key="shop", severity="yellow", detected_at=now, last_seen_at=now,
                           metric_after=D(2), details=details))
        await db.commit()
    body = (await client.get("/api/v1/anomalies", params={"area": "finance"}, headers=auth(tokens))).json()
    assert sorted(i["type"] for i in body["items"]) == ["accrual_gaps", "uncategorized"] and body["total"] == 2
    assert (await client.get("/api/v1/anomalies", headers=auth(tokens))).json()["total"] == 3
