"""Банк (шаг 3.11): разбор выписки 1С (кодировки, приход / расход, чужие счета, ошибки), загрузка без дублей, поступление
от Ozon закрывает выплату Ozon без второй операции, сверка, похожие ручные операции, переводы между своими счетами,
подсказка «сколько реально пришло». Выписки — синтетические (правило 11)."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.api.v1 import bank as bank_api
from app.integrations.bank.statement_1c import StatementError, parse
from app.services import bank as bank_svc
from tests.test_payouts import add_days
from tests.test_plan_calendar import TODAY, call, fixed_today  # noqa: F401 — фикстура «сегодня»
from tests.test_pnl_api import user_id
from tests.test_report_pnl import WEEK, auth, upload

D = Decimal
OUR = "40702810900000012345"
CARD = "40817810100000054321"
OZON_ACC = "40702810400000099999"
OZON_INN = "7704217370"


@pytest.fixture(autouse=True)
def bank_today(monkeypatch):
    monkeypatch.setattr(bank_api, "today_msk", lambda: TODAY)


def doc(n, day, amount, payer, receiver, payer_inn="", payer_name="", receiver_inn="", receiver_name="", purpose=""):
    return [
        "СекцияДокумент=Платежное поручение", f"Номер={n}", f"Дата={day}", f"Сумма={amount}",
        f"ПлательщикСчет={payer}", f"Плательщик=ИНН {payer_inn} {payer_name}".strip(), f"ПлательщикИНН={payer_inn}",
        f"Плательщик1={payer_name}", "ПлательщикБИК=044525104", 'ПлательщикБанк1=ООО "Банк Точка"' if payer == OUR else "",
        f"ПолучательСчет={receiver}", f"ПолучательИНН={receiver_inn}", f"Получатель1={receiver_name}",
        "ПолучательБИК=044525104", 'ПолучательБанк1=ООО "Банк Точка"' if receiver == OUR else "",
        f"ДатаСписано={day}" if payer in (OUR, CARD) else "", f"ДатаПоступило={day}" if receiver in (OUR, CARD) else "",
        f"НазначениеПлатежа={purpose}", "КонецДокумента",
    ]


def statement(docs, account=OUR, start="01.10.2026", end="14.10.2026", opening="100000.00", closing=None,
              encoding="Windows", version="1.03") -> str:
    lines = ["1CClientBankExchange", f"ВерсияФормата={version}", f"Кодировка={encoding}", "Отправитель=Тестовый банк",
             f"ДатаНачала={start}", f"ДатаКонца={end}", f"РасчСчет={account}", "Документ=Платежное поручение",
             "СекцияРасчСчет", f"ДатаНачала={start}", f"ДатаКонца={end}", f"РасчСчет={account}",
             f"НачальныйОстаток={opening}"]
    if closing is not None:
        lines.append(f"КонечныйОстаток={closing}")
    lines.append("КонецРасчСчет")
    for d in docs:
        lines += [ln for ln in d if ln]
    lines.append("КонецФайла")
    return "\n".join(lines) + "\n"


OZON_IN = doc(101, "02.10.2026", "50000.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"', purpose="Выплата по договору")
RENT = doc(7, "05.10.2026", "30000.00", OUR, "40802810500000077777", receiver_inn="500100732259",
           receiver_name="ИП Петров Петр Петрович", purpose="Аренда склада за октябрь")
CLIENT = doc(12, "06.10.2026", "1500,50", "40702810700000011111", OUR, "7701234567", 'ООО "Ромашка"', purpose="Оплата по счёту 15")
FOREIGN = doc(8, "07.10.2026", "10.00", "40702810000000000001", "40702810000000000002")


# ---------- разбор файла ----------


def test_parse_directions_encodings_and_foreign():
    text = statement([OZON_IN, RENT, CLIENT, FOREIGN], closing="121500.50")
    for encoding, raw in (("cp1251", text.encode("cp1251")), ("utf-8", text.encode("utf-8")),
                          ("utf-8", b"\xef\xbb\xbf" + text.encode("utf-8")),
                          ("cp866", text.replace("Кодировка=Windows", "Кодировка=DOS").encode("cp866"))):
        st = parse(raw)
        assert st.encoding == encoding and st.version == "1.03"
        assert [(d.number, d.day, d.amount) for d in st.documents] == [
            ("101", date(2026, 10, 2), D("50000.00")), ("7", date(2026, 10, 5), D("-30000.00")),
            ("12", date(2026, 10, 6), D("1500.50"))]
    st = parse(text.encode("cp1251"))
    acc = st.accounts[0]
    assert (acc.number, acc.opening, acc.closing, acc.date_from) == (OUR, D("100000.00"), D("121500.50"), date(2026, 10, 1))
    ozon, rent, _ = st.documents
    assert (ozon.counterparty, ozon.counterparty_inn, ozon.counterparty_account) == ('ООО "ИНТЕРНЕТ РЕШЕНИЯ"', OZON_INN, OZON_ACC)
    assert (rent.counterparty, rent.purpose, rent.own_bank) == ("ИП Петров Петр Петрович", "Аренда склада за октябрь", 'ООО "Банк Точка"')
    assert st.skipped == ["Документ № 8: ни плательщик, ни получатель — не счета этой выписки"]  # чужие счета


def test_parse_errors():
    with pytest.raises(StatementError, match="Это не выписка в формате 1С"):
        parse("Дата;Сумма\n01.10.2026;100\n".encode("cp1251"))
    with pytest.raises(StatementError, match="Файл пустой"):
        parse(b"")
    with pytest.raises(StatementError, match="не указан расчётный счёт"):
        parse("1CClientBankExchange\nВерсияФормата=1.02\nКонецФайла\n".encode("cp1251"))
    bad = doc(9, "32.10.2026", "abc", OUR, "40702810000000000003")
    st = parse(statement([bad, doc(10, "", "100", OUR, "40702810000000000003")]).encode("cp1251"))
    assert st.documents == [] and len(st.skipped) == 2
    assert st.skipped[0].startswith("Документ № 9: сумма «abc»") and st.skipped[1] == "Документ № 10: нет даты документа"


# ---------- загрузка ----------


async def setup(client, tokens, balance=None):
    accounts = [{"name": "Расчётный счёт", "kind": "checking", **({"balance": balance} if balance else {})}]
    await call(client, tokens, "POST", "/setup", json={
        "supplier_payment": "upfront", "accounts": accounts, "ozon_account": 0, "categories": ["rent"]})
    return {a["name"]: a["id"] for a in await call(client, tokens, "GET", "/accounts")}


async def send(client, tokens, text, dry_run=True, encoding="cp1251", **params):
    r = await client.post("/api/v1/bank/statements/upload", params={"dry_run": dry_run, **params}, headers=auth(tokens),
                          files={"file": ("выписка.txt", text.encode(encoding), "text/plain")})
    assert r.status_code == 200, r.text
    return r.json()


async def test_upload_new_account_preview_apply_and_no_duplicates(client, tokens):
    await setup(client, tokens)
    text = statement([OZON_IN, RENT, CLIENT, FOREIGN], closing="121500.50")
    preview = await send(client, tokens, text)
    assert preview["applied"] is False and preview["date_from"] == "2026-10-01" and preview["encoding"] == "cp1251"
    acc = preview["accounts"][0]
    assert (acc["masked"], acc["name"], acc["opening"]) == ("···2345", "Банк Точка ···2345", "100000.00")
    assert preview["counts"] == {"new": 3, "duplicate": 0, "ozon": 1, "ozon_linked": 0, "transfer": 0, "manual": 0,
                                 "skipped": 1}
    assert [c["name"] for c in preview["candidates"]] == ["Расчётный счёт"]  # один счёт без номера — подсказываем его
    assert preview["suggested_account_id"] is not None

    # загружаем в новый счёт «из банка»: остаток на начало — из выписки, остаток на конец сходится
    done = await send(client, tokens, text, dry_run=False)
    assert done["applied"] is True and done["warnings"] == []
    accounts = {a["name"]: a for a in await call(client, tokens, "GET", "/accounts")}
    bank = accounts["Банк Точка ···2345"]
    assert (bank["source"], bank["number"], bank["opening"]) == ("bank", OUR, {"amount": "100000.00", "as_of": "2026-10-01"})
    assert bank["balance"] == "121500.50"
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["items"]
    by_amount = {o["amount"]: o for o in ops}
    assert set(by_amount) == {"50000.00", "-30000.00", "1500.50"} and all(o["source"] == "bank" for o in ops)
    assert by_amount["50000.00"]["category"]["name"] == "Поступления от маркетплейсов"  # Ozon — по ИНН из справочника
    assert by_amount["50000.00"]["counterparty"] == "Ozon"
    assert by_amount["-30000.00"]["comment"] == "Аренда склада за октябрь"

    # тот же файл и файл за пересекающийся период — дублей нет
    again = await send(client, tokens, text, dry_run=False)
    assert again["counts"]["new"] == 0 and again["counts"]["duplicate"] == 3 and again["already_loaded"] is not None
    more = statement([CLIENT, doc(13, "14.10.2026", "700.00", OUR, "40702810000000000009", receiver_name="Связь")],
                     start="06.10.2026", end="14.10.2026", opening="151500.50")
    later = await send(client, tokens, more, dry_run=False)
    assert (later["counts"]["new"], later["counts"]["duplicate"]) == (1, 1)
    assert len((await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["items"]) == 4
    statements = await call(client, tokens, "GET", "/statements", base="/api/v1/bank")
    assert len(statements) == 2  # тот же файл второй раз не сохраняется
    txs = await call(client, tokens, "GET", "/transactions", base="/api/v1/bank", params={"date_from": "2026-10-01"})
    assert txs["total"] == 4 and {t["kind"] for t in txs["items"]} == {"ozon", "regular"}

    # операцию банка не правят в сумме и не удаляют; статью — можно
    op = by_amount["-30000.00"]["op_id"]
    r = await client.patch(f"/api/v1/planning/operations/{op}", json={"amount": "1"}, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"].startswith("Операция из выписки банка")
    cats = {c["code"]: c["id"] for c in await call(client, tokens, "GET", "/categories") if c["code"]}
    await call(client, tokens, "PATCH", f"/operations/{op}", json={"category_id": cats["rent"]})
    r = await client.delete(f"/api/v1/planning/operations/{op}", headers=auth(tokens))
    assert r.status_code == 400 and "удалить нельзя" in r.json()["detail"]


async def test_upload_into_chosen_account_and_wrong_number(client, tokens):
    accounts = await setup(client, tokens, balance="100000")
    chosen = accounts["Расчётный счёт"]
    done = await send(client, tokens, statement([RENT]), dry_run=False, account_id=chosen)
    assert done["accounts"][0]["account"]["name"] == "Расчётный счёт"
    acc = next(a for a in await call(client, tokens, "GET", "/accounts", base="/api/v1/bank") if a["id"] == chosen)
    assert (acc["number"], acc["source"], acc["bank_name"]) == (OUR, "bank", 'ООО "Банк Точка"')
    # остаток был указан на сегодня, выписка с 1.10 до него доходит — остаток на начало теперь из выписки
    assert acc["balance"] == "70000.00" and done["warnings"][0].startswith("«Расчётный счёт»: начальный остаток — из выписки")
    # выписка за старый период с пробелом до остатка — остаток пользователя не трогаем
    old = statement([], start="01.08.2026", end="31.08.2026", opening="5.00", closing="5.00")
    await send(client, tokens, old, dry_run=False, account_id=chosen)
    assert next(a for a in await call(client, tokens, "GET", "/accounts") if a["id"] == chosen)["opening"] == {
        "amount": "100000.00", "as_of": "2026-10-01"}
    # счёт уже с другим номером — ошибка понятная
    r = await client.post("/api/v1/bank/statements/upload", params={"account_id": chosen}, headers=auth(tokens),
                          files={"file": ("x.txt", statement([], account=CARD).encode("cp1251"), "text/plain")})
    assert r.status_code == 400 and "другой номер" in r.json()["detail"]
    r = await client.post("/api/v1/bank/statements/upload", headers=auth(tokens),
                          files={"file": ("x.csv", "Дата;Сумма".encode("cp1251"), "text/csv")})
    assert r.status_code == 400 and r.json()["detail"].startswith("Это не выписка в формате 1С")


async def test_ozon_receipt_confirms_payout_without_second_operation(client, tokens, sessions):
    await setup(client, tokens)
    uid = await user_id(client, tokens)
    # выплаты по данным Ozon: 30.09 — 50 000, 07.10 — 40 000 (баланс у Ozon по дням)
    await add_days(sessions, uid, {date(2026, 9, 30): D(50000), date(2026, 10, 7): D(40000)},
                   {date(2026, 9, 28): D(7000), date(2026, 10, 5): D(7000)}, TODAY)
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-09-01"}))["items"]
    assert sorted(o["amount"] for o in ops if o["source"] == "ozon_payout") == ["40000.00", "50000.00"]

    # выписка: поступление 50 000 через 2 дня и 39 800 (расхождение 200) — подтверждают выплаты; 12 000 — без пары
    ozon1 = doc(201, "02.10.2026", "50000.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"')
    ozon2 = doc(202, "08.10.2026", "39800.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"')
    ozon3 = doc(203, "13.10.2026", "12000.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"')
    preview = await send(client, tokens, statement([ozon1, ozon2, ozon3]))
    assert (preview["counts"]["ozon"], preview["counts"]["ozon_linked"]) == (3, 2)
    assert [r["ozon_payout"] for r in preview["rows"]] == ["2026-09-30", "2026-10-07", None]
    await send(client, tokens, statement([ozon1, ozon2, ozon3]), dry_run=False)

    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-09-01"}))["items"]
    ozon_ops = [o for o in ops if o["counterparty"] == "Ozon"]
    assert sorted((o["day"], o["amount"], o["source"]) for o in ozon_ops) == [
        ("2026-10-02", "50000.00", "bank"), ("2026-10-08", "39800.00", "bank"), ("2026-10-13", "12000.00", "bank")]
    # выплата Ozon за 12.10 появилась в данных Ozon позже — это то же поступление, второй операции нет
    await add_days(sessions, uid, {date(2026, 10, 12): D(12000)}, {date(2026, 10, 12): D(7000)}, TODAY)
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-09-01"}))["items"]
    assert len([o for o in ops if o["counterparty"] == "Ozon"]) == 3

    check = await call(client, tokens, "GET", "/ozon-check", base="/api/v1/bank",
                       params={"date_from": "2026-09-01", "date_to": "2026-10-14"})
    assert [(r["payout_day"], r["status"], r["diff"]) for r in check["rows"]] == [
        ("2026-10-12", "ok", "0.00"), ("2026-10-07", "diff", "-200.00"), ("2026-09-30", "ok", "0.00")]
    assert check["counts"] == {"ok": 2, "diff": 1}


async def test_manual_duplicates_suggested_not_merged_silently(client, tokens):
    accounts = await setup(client, tokens, balance="100000")
    acc = accounts["Расчётный счёт"]
    cats = {c["code"]: c["id"] for c in await call(client, tokens, "GET", "/categories") if c["code"]}
    manual = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "30000", "day": "2026-10-04", "account_id": acc, "category_id": cats["rent"],
        "counterparty": "Петров", "comment": "аренда, октябрь"})
    other = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "income", "amount": "1500.50", "day": "2026-10-06", "account_id": acc})
    preview = await send(client, tokens, statement([RENT, CLIENT]))
    assert preview["counts"]["manual"] == 2  # похожи — предлагаем, но не объединяем сами
    await send(client, tokens, statement([RENT, CLIENT]), dry_run=False, account_id=acc)
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["items"]
    assert len(ops) == 4  # и ручные, и из банка — пока пользователь не решил

    pairs = await call(client, tokens, "GET", "/matches", base="/api/v1/bank")
    assert [(p["manual"]["op_id"], p["bank"]["amount"]) for p in pairs] == [
        (manual["op_id"], "-30000.00"), (other["op_id"], "1500.50")]
    rent_pair = pairs[0]
    await call(client, tokens, "POST", "/matches/merge", 204, base="/api/v1/bank",
               json={"bank_op_id": rent_pair["bank"]["op_id"], "manual_op_id": manual["op_id"]})
    await call(client, tokens, "POST", "/matches/dismiss", 204, base="/api/v1/bank",
               json={"bank_op_id": pairs[1]["bank"]["op_id"], "manual_op_id": other["op_id"]})
    assert await call(client, tokens, "GET", "/matches", base="/api/v1/bank") == []
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["items"]
    rent = next(o for o in ops if o["amount"] == "-30000.00")
    assert len(ops) == 3 and rent["source"] == "bank" and rent["category"]["name"] == "Аренда"  # статья ручной — у банка
    assert rent["comment"].startswith("аренда, октябрь")
    history = await call(client, tokens, "GET", f"/operations/{manual['op_id']}/history")
    assert history[0]["deleted_at"] is not None  # ручная — скрыта, в истории осталась


async def test_transfer_between_own_accounts(client, tokens):
    await setup(client, tokens)
    out = doc(301, "03.10.2026", "5000.00", OUR, CARD, receiver_name="Карта")
    await send(client, tokens, statement([out, RENT]), dry_run=False)
    card_in = doc(301, "03.10.2026", "5000.00", OUR, CARD, payer_name="Счёт")
    await send(client, tokens, statement([card_in], account=CARD, opening="0.00"), dry_run=False)
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["items"]
    transfer = next(o for o in ops if o["transfer"])
    assert transfer["transfer"]["direction"] == "out" and transfer["transfer"]["account"]["name"].endswith("···4321")
    totals = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["totals"]
    assert totals == {"income": "0.00", "expense": "-30000.00", "net": "-30000.00"}  # перевод — не расход
    flow = await call(client, tokens, "GET", "/cash-flow", params={"date_from": "2026-10-01", "date_to": "2026-10-14"})
    assert flow["expense"] == "-30000.00" and flow["income"] == "0.00"


async def test_report_hint_from_bank_ozon_receipt(client, tokens, monkeypatch):
    from app.services import ozon_payouts

    monkeypatch.setattr(ozon_payouts, "today_msk", lambda: TODAY)
    await setup(client, tokens)
    report = await upload(client, tokens, WEEK)  # продажи 07–13.09
    detail = await call(client, tokens, "GET", f"/{report['id']}", base="/api/v1/reports")
    assert detail["bank_hint"] is None  # выписки нет — подсказки нет
    # стандартный график Ozon: за неделю продаж — через 3 недели, в среду 30.09
    receipt = doc(401, "30.09.2026", "10500.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"')
    other = doc(402, "07.10.2026", "9000.00", OZON_ACC, OUR, OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"')
    await send(client, tokens, statement([receipt, other], start="28.09.2026"), dry_run=False)
    detail = await call(client, tokens, "GET", f"/{report['id']}", base="/api/v1/reports")
    hint = detail["bank_hint"]
    assert (hint["amount"], hint["count"], hint["lag_weeks"], hint["date_from"]) == ("10500.00", 1, 3, "2026-09-30")
    assert detail["manual_total"] is None  # только подсказка — сохраняется по подтверждению
    assert bank_svc is not None
