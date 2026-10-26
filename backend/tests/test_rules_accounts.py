"""«Операции» (шаг 10.20): правила — условия «счёт» и «источник», журнал применения и ошибок, правило не срабатывает
(его операции забирает правило выше), статья правила в архиве, проверка перед массовым применением, операции правила;
план-факт — предложения «это оплата плана …», принять и отклонить; счета — сверка расчётного остатка с выпиской
(расхождение и с какого дня), история по счёту с остатком, нераспознанные операции."""
from datetime import date
from decimal import Decimal

from app.engine import categorize as eng
from app.engine import payment_calendar as cal
from app.engine import reconcile as rec
from tests.test_bank import CLIENT, OUR, RENT, bank_today, send, statement  # noqa: F401 — фикстура «сегодня» банка
from tests.test_plan_calendar import call, fixed_today  # noqa: F401 — фикстура «сегодня»
from tests.test_report_pnl import auth
from tests.test_rules import SUBSCRIPTION, TAX

D = Decimal
URL = "/api/v1/planning"
OCT = {"date_from": "2026-10-01", "date_to": "2026-10-14"}


async def err(client, tokens, method, path, code=400, **kw) -> str:
    r = await client.request(method, f"{URL}{path}", headers=auth(tokens), **kw)
    assert r.status_code == code, r.text
    return r.json()["detail"]


async def setup(client, tokens, balance=None):
    accounts = [{"name": "Расчётный счёт", "kind": "checking", **({"balance": balance} if balance else {})},
                {"name": "Наличные", "kind": "cash"}]
    await call(client, tokens, "POST", "/setup", json={
        "supplier_payment": "upfront", "accounts": accounts, "ozon_account": 0,
        "categories": ["rent", "subscriptions", "other_expense", "other_income", "taxes", "salary"]})
    await call(client, tokens, "PUT", "/settings", json={"gen_ozon": False, "gen_taxes": False})
    cats = {c["code"]: c["id"] for c in await call(client, tokens, "GET", "/categories") if c["code"]}
    accounts = {a["name"]: a["id"] for a in await call(client, tokens, "GET", "/accounts")}
    return cats, accounts


# ---------- движок ----------


def test_engine_account_source_and_coverage():
    f_bank = eng.Facts(D("-30000"), "ИП Петров", purpose="Аренда склада", account_id="A", source="bank")
    f_cash = eng.Facts(D("-500"), purpose="аренда коврика", account_id="C", source="manual")
    by_source = eng.Rule("1", "out", "rent", priority=10, purpose="аренд", source="bank")
    by_account = eng.Rule("2", "out", "other", priority=20, account_id="C")
    petrov = eng.Rule("3", "out", "other", priority=30, counterparty="Петров")
    assert (eng.fits(by_source, f_bank), eng.fits(by_source, f_cash)) == (True, False)
    assert (eng.fits(by_account, f_bank), eng.fits(by_account, f_cash)) == (False, True)
    assert eng.has_conditions(eng.Rule("4", "out", "x", source="manual"))  # источник — тоже условие
    rules = [by_source, by_account, petrov]
    stats = eng.coverage(rules, [f_bank, f_cash], lambda f: (eng.pick(rules, f) or eng.Rule("", "", "")).id or None)
    assert {k: (v["fits"], v["wins"], dict(v["taken_by"])) for k, v in stats.items()} == {
        "1": (1, 1, {}), "2": (1, 1, {}), "3": (1, 0, {"1": 1})}  # «Петрова» забирает правило 1 — оно выше


def item(key, day, amount, **kw):
    kind = "income" if D(amount) > 0 else "expense"
    return cal.Item(key=key, source="manual", title=key, planned_day=day, amount=D(amount), kind=kind, **kw)


def test_suggest_plan_fact():
    today = date(2026, 10, 14)
    rent = item("rent", date(2026, 10, 5), "-30000", category_id="rent", counterparty="ИП Петров")
    courier = item("courier", date(2026, 10, 12), "-2000", category_id="delivery")
    future = item("future", date(2026, 11, 20), "-1000", category_id="rent")
    ops = [
        cal.Op("o1", date(2026, 10, 9), D("-27000"), counterparty="Петров Петр ИП"),  # без статьи, на 4 дня позже
        cal.Op("o2", date(2026, 10, 12), D("-2100"), category_id="delivery"),  # та же статья, +5 %
        cal.Op("o3", date(2026, 10, 6), D("-30000"), category_id="salary"),  # другая статья — не оплата аренды
        cal.Op("o4", date(2026, 10, 13), D("-1000"), category_id="rent"),  # платёж из будущего месяца не ловим
    ]
    cal.match([rent, courier, future], ops, {})
    assert rent.fact is None and courier.fact is not None  # ± 10 % и ± 3 дня — закрыт сам
    out = cal.suggest([rent, courier, future], ops, today)
    assert [(s.item.key, s.op.op_id) for s in out] == [("rent", "o1")]
    assert out[0].reasons == ["сумма отличается на 3000.00 ₽ (10.0 %)", "на 4 дня позже срока", "тот же контрагент",
                              "у операции нет статьи — поставим статью платежа"]
    assert cal.suggest([rent], ops, today, dismissed={("rent", "o1")}) == []  # «нет» — больше не предлагаем
    far = cal.Op("o5", date(2026, 10, 30), D("-30000"))  # дальше 10 дней
    assert cal.suggest([rent], [far], today) == []


def test_reconcile_engine():
    points = rec.points_from([(date(2026, 10, 1), "opening", D(100000)), (date(2026, 10, 10), "closing", D(71500))])
    assert points == [rec.Point(date(2026, 9, 30), D(100000)), rec.Point(date(2026, 10, 10), D(71500))]
    bank = {date(2026, 10, 5): D(-30000), date(2026, 10, 6): D(1500)}
    service = {**bank, date(2026, 10, 7): D(-2000)}  # лишняя ручная операция
    r = rec.reconcile(points, bank, (date(2026, 10, 1), D(100000)), service)
    assert (r.status, r.since, r.matched_until, r.from_start, r.jump, r.last.diff) == (
        "diff", date(2026, 10, 7), date(2026, 10, 6), False, D("-2000.00"), D("-2000.00"))
    assert rec.reconcile(points, bank, (date(2026, 10, 1), D(100000)), bank).status == "ok"
    # неверный начальный остаток — расходится с первого дня данных банка
    r = rec.reconcile(points, bank, (date(2026, 10, 1), D(90000)), bank)
    assert (r.since, r.from_start, r.last.diff) == (date(2026, 9, 30), True, D("-10000.00"))
    # операций банка за 6.10 нет — между точками дни не сравниваем, только сами точки
    r = rec.reconcile(points, {date(2026, 10, 5): D(-30000)}, (date(2026, 10, 1), D(100000)), service)
    assert ([d.day for d in r.days], r.since, r.matched_until) == (
        [date(2026, 9, 30), date(2026, 10, 10)], date(2026, 10, 10), date(2026, 9, 30))
    assert rec.reconcile([], bank, None, bank).status == "no_bank"
    assert rec.reconcile(points, bank, None, bank).status == "no_opening"


# ---------- правила: условия, журнал, ошибки ----------


async def test_rules_journal_conditions_and_problems(client, tokens):
    cats, accounts = await setup(client, tokens)
    await send(client, tokens, statement([RENT, CLIENT, SUBSCRIPTION, TAX]), dry_run=False)
    cash = accounts["Наличные"]
    manual = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "500", "day": "2026-10-09", "account_id": cash, "comment": "аренда коврика"})
    runs = await call(client, tokens, "GET", "/rules/runs")
    assert [(r["trigger"], r["checked"], r["changed"]) for r in runs] == [("bank_file", 4, 1)]  # налог — встроенное
    assert runs[0]["details"][0]["after"] == "Налоги и взносы" and runs[0]["details"][0]["by"] == "auto"

    # «аренд» только из банка: ручная «аренда коврика» не подходит; правило по счёту «Наличные»
    a = (await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "purpose": "аренд", "source": "bank", "category_id": cats["rent"]}))
    assert a["applied"] == 1 and a["rule"]["source"] == "bank"
    b = await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "account_id": cash, "category_id": cats["other_expense"]})
    assert b["applied"] == 1 and b["rule"]["account"]["name"] == "Наличные"
    page = await call(client, tokens, "GET", "/journal", params={**OCT, "rule_id": a["rule"]["id"]})
    assert [r["amount"] for r in page["items"]] == ["-30000.00"]  # операции правила
    page = await call(client, tokens, "GET", "/journal", params={**OCT, "rule_id": b["rule"]["id"]})
    assert [r["operation"]["op_id"] for r in page["items"]] == [manual["op_id"]]
    runs = await call(client, tokens, "GET", "/rules/runs")
    last = runs[0]
    assert (last["trigger"], last["title"], last["changed"], last["rule"]["number"]) == (
        "rule_created", "Добавлено правило", 1, 2)
    assert last["rule"]["text"] == "Платёж · счёт «Наличные» → Прочие расходы"
    assert (last["details"][0]["before"], last["details"][0]["after"]) == (None, "Прочие расходы")

    # «Петров» ниже правила «аренд»: все его операции забирает правило №1
    c = await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "counterparty": "Петров", "category_id": cats["other_expense"]})
    assert c["applied"] == 0
    rules = {r["number"]: r for r in (await call(client, tokens, "GET", "/rules"))["rules"]}
    assert (rules[3]["matches"], rules[3]["operations"]) == (1, 0)
    assert rules[3]["problem"] == ("Не срабатывает: все подходящие операции (1) забирает правило №1 — оно выше. "
                                   "Поднимите это правило или уточните условия")
    assert rules[1]["problem"] is None

    # статья правила в архиве: проверка показывает, что поменяется, применение — пишет журнал
    d = await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "purpose": "подписк", "category_id": cats["subscriptions"]})
    assert d["applied"] == 1
    await call(client, tokens, "PATCH", f"/categories/{cats['subscriptions']}", json={"archived": True})
    check = await call(client, tokens, "POST", "/rules/check")
    assert (check["changed"], check["examples"][0]["before"], check["examples"][0]["after"]) == (
        1, "Подписки и сервисы", None)
    assert any(p["message"].startswith("Статья «Подписки и сервисы» в архиве") for p in check["problems"])
    assert (await call(client, tokens, "GET", "/rules/runs"))[0]["trigger"] == "rule_created"  # проверка не пишет
    applied = await call(client, tokens, "POST", "/rules/apply")
    assert applied == {"changed": 1}
    run = (await call(client, tokens, "GET", "/rules/runs"))[0]
    assert (run["trigger"], run["changed"], run["checked"]) == ("manual", 1, 4)  # налог — встроенное, не пересчитывается
    assert {p["rule"]["number"] for p in run["problems"]} == {3, 4}

    # чужой счёт в условии — нет
    assert await err(client, tokens, "POST", "/rules", 404, json={
        "direction": "out", "account_id": "00000000-0000-0000-0000-000000000001",
        "category_id": cats["rent"]}) == "Счёт не найден"


# ---------- план-факт ----------


async def test_plan_fact_suggest_accept_dismiss(client, tokens):
    cats, accounts = await setup(client, tokens, balance="100000")
    acc = accounts["Расчётный счёт"]
    await call(client, tokens, "POST", "/plans", 201, json={
        "kind": "expense", "title": "Аренда склада", "amount": "30000", "start_date": "2026-10-05",
        "category_id": cats["rent"], "account_id": acc, "counterparty": "ИП Петров"})
    await call(client, tokens, "POST", "/plans", 201, json={
        "kind": "expense", "title": "Хостинг", "amount": "990", "start_date": "2026-10-08",
        "category_id": cats["subscriptions"]})
    await call(client, tokens, "POST", "/plans", 201, json={
        "kind": "expense", "title": "Курьер", "amount": "2000", "start_date": "2026-10-12", "counterparty": "СДЭК"})
    rent_op = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "28000", "day": "2026-10-09", "account_id": acc, "counterparty": "Петров ИП"})
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "990", "day": "2026-10-08", "account_id": acc, "category_id": cats["subscriptions"]})
    courier_op = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "2300", "day": "2026-10-13", "account_id": acc, "counterparty": "СДЭК"})
    params = {"date_from": "2026-10-01", "date_to": "2026-10-20"}

    body = await call(client, tokens, "GET", "/plan-fact", params=params)
    rows = {r["title"]: r for r in body["rows"]}
    assert (rows["Хостинг"]["status"], rows["Хостинг"]["how"], rows["Хостинг"]["diff"]) == ("paid", "auto", "0.00")
    rent = rows["Аренда склада"]
    assert (rent["status"], rent["fact"]) == ("overdue", None)
    assert rent["suggestion"]["op"]["op_id"] == rent_op["op_id"]
    assert rent["suggestion"]["reasons"] == ["сумма отличается на 2000.00 ₽ (6.7 %)", "на 4 дня позже срока",
                                             "тот же контрагент", "у операции нет статьи — поставим статью платежа"]
    assert rent["suggestion"]["category_fix"] == "Аренда"
    assert rows["Курьер"]["suggestion"]["op"]["op_id"] == courier_op["op_id"]
    # курьер — срок 2 дня назад: «ожидается» (шаг 10.24), аренда — просрочена
    assert body["counts"] == {"paid": 1, "partial": 0, "overdue": 1, "expected": 1, "planned": 0, "suggestions": 2}

    accepted = await call(client, tokens, "POST", "/plan-fact/accept", json={"key": rent["key"], "op_id": rent_op["op_id"]})
    assert accepted == {"title": "Аренда склада", "category_set": "Аренда"}
    await call(client, tokens, "POST", "/plan-fact/dismiss", 204, json={"key": rows["Курьер"]["key"],
                                                                          "op_id": courier_op["op_id"]})
    body = await call(client, tokens, "GET", "/plan-fact", params=params)
    rows = {r["title"]: r for r in body["rows"]}
    # оплата 28 000 при плане 30 000 — оплачен частично, остаток 2 000 (шаг 10.24)
    assert (rows["Аренда склада"]["status"], rows["Аренда склада"]["how"], rows["Аренда склада"]["diff"]) == (
        "partial", "marked", "2000.00")
    assert rows["Аренда склада"]["fact"]["category"] == "Аренда" and rows["Аренда склада"]["days_late"] == 4
    assert rows["Курьер"]["suggestion"] is None and body["counts"]["suggestions"] == 0
    # статья у операции поставлена вручную — новая версия, прежняя в истории
    history = await call(client, tokens, "GET", f"/operations/{rent_op['op_id']}/history")
    assert [h["category"] for h in history] == ["Аренда", None]
    assert await err(client, tokens, "POST", "/plan-fact/accept", json={
        "key": rows["Курьер"]["key"], "op_id": "00000000-0000-0000-0000-000000000001"}, code=404) == "Операция не найдена"


# ---------- счета: сверка, история, нераспознанные ----------


async def test_reconcile_history_unrecognized(client, tokens):
    cats, accounts = await setup(client, tokens)
    acc = accounts["Расчётный счёт"]
    await send(client, tokens, statement([RENT, CLIENT], closing="71500.50"), dry_run=False, account_id=acc)
    found = {r["account"]["name"]: r for r in await call(client, tokens, "GET", "/accounts/reconcile")}
    assert (found["Расчётный счёт"]["status"], found["Расчётный счёт"]["bank"]) == ("ok", "71500.50")
    assert found["Расчётный счёт"]["hint"] == "Сходится с банком на 14.10.2026"
    assert found["Наличные"]["status"] == "no_bank"

    dup = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "2000", "day": "2026-10-07", "account_id": acc, "comment": "курьер"})
    r = {x["account"]["name"]: x for x in await call(client, tokens, "GET", "/accounts/reconcile")}["Расчётный счёт"]
    assert (r["status"], r["diff"], r["since"], r["matched_until"], r["adjacent"]) == (
        "diff", "-2000.00", "2026-10-07", "2026-10-06", True)
    assert r["hint"] == ("По сервису на 2 000 ₽ меньше, чем в банке на 14.10.2026. Сходилось по 06.10.2026, "
                         "расходится с 07.10.2026")
    assert [(s["op_id"], s["likely"]) for s in r["suspects"]] == [(dup["op_id"], True)]

    # история по счёту: остаток после каждой операции, перевод, фильтр
    await call(client, tokens, "POST", "/operations", 201, json={
        "type": "transfer", "amount": "500", "day": "2026-10-08", "account_id": acc,
        "to_account_id": accounts["Наличные"]})
    h = await call(client, tokens, "GET", f"/accounts/{acc}/history", params=OCT)
    assert h["opening"] == "100000.00" and h["closing"] == "69000.50"
    assert [(i["day"], i["amount"], i["type"], i["balance"]) for i in h["items"]] == [
        ("2026-10-08", "-500.00", "transfer", "69000.50"), ("2026-10-07", "-2000.00", "expense", "69500.50"),
        ("2026-10-06", "1500.50", "income", "71500.50"), ("2026-10-05", "-30000.00", "expense", "70000.00")]
    assert h["items"][0]["transfer_account"] == "Наличные"
    assert h["totals"] == {"income": "1500.50", "expense": "-32000.00", "transfer_in": "0.00", "transfer_out": "-500.00"}
    only = await call(client, tokens, "GET", f"/accounts/{acc}/history", params={**OCT, "type": "income"})
    assert [i["amount"] for i in only["items"]] == ["1500.50"]

    # удалили дубль — снова сходится
    await call(client, tokens, "DELETE", f"/operations/{dup['op_id']}", 204)
    await call(client, tokens, "DELETE", f"/operations/{h['items'][0]['op_id']}", 204)
    r = {x["account"]["name"]: x for x in await call(client, tokens, "GET", "/accounts/reconcile")}["Расчётный счёт"]
    assert r["status"] == "ok"

    # нераспознанные: операции банка без статьи — с назначением, ИНН и заготовкой правила
    un = await call(client, tokens, "GET", "/unrecognized")
    assert un["total"] == 2 and un["by_account"] == [{"id": acc, "name": "Расчётный счёт", "count": 2}]
    rent = next(i for i in un["items"] if i["amount"] == "-30000.00")
    assert (rent["purpose"], rent["inn"], rent["rule"]) == (
        "Аренда склада за октябрь", "500100732259", {"direction": "out", "counterparty_inn": "500100732259",
                                                      "counterparty": None})
    await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "counterparty_inn": "500100732259", "category_id": cats["rent"]})
    assert (await call(client, tokens, "GET", "/unrecognized"))["total"] == 1
    assert await err(client, tokens, "GET", f"/accounts/{cats['rent']}/history", code=404) == "Счёт не найден"
