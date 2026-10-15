"""Банки по API (шаг 3.12) на фейковых ответах банков (respx): Точка — асинхронная выписка, Т-Банк — порции с курсором,
ошибки доступа понятным текстом, токен не попадает в ответы и логи, подключение и замена токена, обновление через общий
runner — без дублей (в том числе с выпиской из файла), поступление Ozon закрывает выплату Ozon, переводы между своими
счетами, налог по реквизитам казначейства закрывает плановый налог (ЕНП), статус в /sync/status. Данные синтетические."""
import json
import logging
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from app.integrations.bank.base import BankAPIError, BankAuthError, BankNetworkError, BankServerError
from app.integrations.bank.tbank import TBankProvider
from app.integrations.bank.tochka import TochkaProvider
from app.models.bank import BankConnection
from app.services import bank_connections
from app.services.sync.runner import run_sync
from tests.test_bank import OUR, OZON_INN, doc, send, statement
from tests.test_payouts import add_days
from tests.test_plan_calendar import TODAY, USN, add_sales, call, days, fixed_today  # noqa: F401 — фикстура
from tests.test_pnl_api import user_id
from tests.test_report_pnl import auth

D = Decimal
TOKEN = "tochka-secret-token-0123456789"
TB_TOKEN = "t.TBANK-SECRET-0123456789"
TOCHKA = "https://enter.tochka.com/uapi/open-banking/v1.0"
TBANK = "https://business.tbank.ru/openapi"
BIK = "044525104"
SECOND = "40702810100000067890"  # второй счёт в Точке
ACC_ID = f"{OUR}/{BIK}"
TREASURY = "03100643000000018500"


async def no_sleep(_):
    return None


@pytest.fixture(autouse=True)
def bank_setup(monkeypatch):
    monkeypatch.setattr(bank_connections, "today_msk", lambda: TODAY)
    monkeypatch.setattr(bank_connections, "PROVIDER_OPTIONS", {"sleep": no_sleep})


def tx(tid, day, amount, direction, inn=None, name=None, account=None, purpose=None, number=None, status="Booked"):
    side = "Debtor" if direction == "Credit" else "Creditor"
    t = {"transactionId": tid, "creditDebitIndicator": direction, "status": status, "documentNumber": number or tid[-3:],
         "transactionTypeCode": "Платежное поручение", "documentProcessDate": day, "description": purpose,
         "Amount": {"amount": amount, "currency": "RUB"}}
    if inn or name:
        t[f"{side}Party"] = {"inn": inn, "name": name}
    if account:
        t[f"{side}Account"] = {"schemeName": "RU.CBR.BBAN", "identification": account}
        t[f"{side}Agent"] = {"schemeName": "RU.CBR.BIK", "identification": BIK}
    return t


OZON_TX = tx("t-ozon", "2026-10-02", 50000, "Credit", OZON_INN, 'ООО "ИНТЕРНЕТ РЕШЕНИЯ"', "40702810400000099999",
             "Выплата по договору", "201")
RENT_TX = tx("t-rent", "2026-10-05", 30000, "Debit", "500100732259", "ИП Петров Петр Петрович", "40802810500000077777",
             "Аренда склада за октябрь", "7")
TAX_TX = tx("t-tax", "2026-10-10", 9000, "Debit", "7727406020", "Казначейство России (ФНС России)", TREASURY,
            "Единый налоговый платеж", "15")
PENDING = tx("t-pend", "2026-10-13", 777, "Credit", "7701234567", "ООО Ромашка", status="Pending")


def tochka_accounts(*numbers):
    return {"Data": {"Account": [
        {"customerCode": "300000092", "accountId": f"{n}/{BIK}", "status": "Enabled", "currency": "RUB",
         "statusUpdateDateTime": "2026-01-01T00:00:00+00:00", "accountType": "Business",
         "accountSubType": "CurrentAccount", "registrationDate": "2024-01-01"} for n in numbers]},
        "Links": {"self": "x"}, "Meta": {"totalPages": 1}}


def tochka(mock, transactions_by_account: dict[str, list], balances: dict[str, float], start: float | None = 100000,
           processing_first=True):
    mock.get(f"{TOCHKA}/accounts").respond(json=tochka_accounts(*transactions_by_account))
    mock.get(f"{TOCHKA}/balances").respond(json={"Data": {"Balance": [
        {"accountId": f"{n}/{BIK}", "creditDebitIndicator": "Credit", "type": t, "dateTime": "2026-10-14T10:00:00",
         "Amount": {"amount": v if t == "ClosingAvailable" else 1, "currency": "RUB"}}
        for n, v in balances.items() for t in ("OpeningAvailable", "ClosingAvailable")]}})
    ids = {}

    def init(request):
        acc = json.loads(request.content)["Data"]["Statement"]["accountId"]
        sid = f"st-{len(ids) + 1}"
        ids[sid] = acc
        return httpx.Response(200, json={"Data": {"Statement": {"accountId": acc, "statementId": sid, "status": "Created",
                                                                "startDateTime": "x", "endDateTime": "y",
                                                                "creationDateTime": "z"}}})

    mock.post(f"{TOCHKA}/statements").mock(side_effect=init)
    polls: dict[str, int] = {}

    def get_statement(request, number, sid):
        polls[sid] = polls.get(sid, 0) + 1
        if processing_first and polls[sid] == 1:
            return httpx.Response(200, json={"Data": {"Statement": [{"accountId": ids[sid], "statementId": sid,
                                                                     "status": "Processing"}]}})
        txs = transactions_by_account[number]
        end = None if start is None else start + sum(
            (t["Amount"]["amount"] if t["creditDebitIndicator"] == "Credit" else -t["Amount"]["amount"])
            for t in txs if t["status"] == "Booked")
        return httpx.Response(200, json={"Data": {"Statement": [{
            "accountId": ids[sid], "statementId": sid, "status": "Ready", "startDateTime": "x", "endDateTime": "y",
            "creationDateTime": "z", "startDateBalance": start, "endDateBalance": end, "Transaction": txs}]}})

    mock.get(url__regex=rf"{TOCHKA}/accounts/(?P<number>\d+)/{BIK}/statements/(?P<sid>st-\d+)").mock(
        side_effect=get_statement)
    return polls


# ---------- провайдеры ----------


async def test_tochka_provider_async_statement_and_mapping():
    with respx.mock(assert_all_called=False) as mock:
        polls = tochka(mock, {OUR: [OZON_TX, RENT_TX, PENDING]}, {OUR: 120000.5})
        async with TochkaProvider(TOKEN, sleep=no_sleep) as p:
            access = await p.check_access()
            acc = access.accounts[0]
            assert (acc.number, acc.bik, acc.bank_id, acc.bank_name) == (OUR, BIK, ACC_ID, "ООО «Банк Точка»")
            assert await p.balances(access.accounts) == {ACC_ID: D("120000.50")}
            st = await p.statement(acc, date(2026, 10, 1), TODAY)
    assert polls == {"st-1": 2}  # сначала «Processing», потом «Ready»
    assert (st.opening, st.closing) == (D("100000.00"), D("120000.00"))
    ozon, rent = st.operations  # Pending — не проведена, не берём
    assert (ozon.bank_id, ozon.day, ozon.amount, ozon.counterparty_inn, ozon.counterparty_account) == (
        "t-ozon", date(2026, 10, 2), D("50000.00"), OZON_INN, "40702810400000099999")
    assert (rent.amount, rent.counterparty, rent.purpose, rent.doc_number, rent.counterparty_bik) == (
        D("-30000.00"), "ИП Петров Петр Петрович", "Аренда склада за октябрь", "7", BIK)


async def test_tbank_provider_cursor_pages():
    page1 = {"balances": {"incoming": 1000, "outgoing": 1500}, "nextCursor": "c2", "operations": [
        {"operationId": "op-1", "operationDate": "2026-10-03T12:00:00+03:00", "typeOfOperation": "Credit",
         "accountAmount": 700, "payPurpose": "Оплата по счёту 15", "documentNumber": "15",
         "payer": {"name": "ООО Ромашка", "inn": "7701234567", "acct": "40702810700000011111", "bicRu": BIK}}]}
    page2 = {"operations": [
        {"operationId": "op-2", "operationDate": "2026-10-04T09:00:00+03:00", "typeOfOperation": "Debit",
         "accountAmount": 200, "description": "Комиссия", "receiver": {"name": "АО ТБанк", "inn": "7710140679"}}]}
    with respx.mock() as mock:
        mock.get(f"{TBANK}/api/v4/bank-accounts").respond(json=[
            {"accountNumber": OUR, "name": "Основной", "currency": "643", "bankBik": "044525974",
             "balance": {"otb": 1500.25}}])
        route = mock.get(f"{TBANK}/api/v1/statement").mock(side_effect=[httpx.Response(200, json=page1),
                                                                         httpx.Response(200, json=page2)])
        async with TBankProvider(TB_TOKEN, sleep=no_sleep) as p:
            [acc] = (await p.check_access()).accounts
            assert await p.balances([acc]) == {OUR: D("1500.25")}
            st = await p.statement(acc, date(2026, 10, 1), TODAY)
    first, second = (r.request for r in route.calls)
    assert first.url.params["withBalances"] == "true" and "cursor" not in first.url.params
    assert second.url.params["cursor"] == "c2" and "withBalances" not in second.url.params  # остатки — в первой порции
    assert first.headers["Authorization"] == f"Bearer {TB_TOKEN}"
    assert (st.opening, st.closing) == (D("1000.00"), D("1500.00"))
    assert [(o.bank_id, o.day, o.amount, o.counterparty_inn) for o in st.operations] == [
        ("op-1", date(2026, 10, 3), D("700.00"), "7701234567"), ("op-2", date(2026, 10, 4), D("-200.00"), "7710140679")]


async def test_errors_are_russian_and_without_token(caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock() as mock:
        route = mock.get(f"{TOCHKA}/accounts")
        route.respond(401, json={"message": f"token {TOKEN} invalid"})
        async with TochkaProvider(TOKEN, sleep=no_sleep) as p:
            with pytest.raises(BankAuthError) as e:
                await p.list_accounts()
        assert "Точка не приняла токен" in e.value.message and TOKEN not in e.value.bank_message

        route.respond(403, json={"Errors": [{"message": "no consent"}]})
        async with TochkaProvider(TOKEN, sleep=no_sleep) as p:
            with pytest.raises(BankAuthError, match="нет нужных разрешений"):
                await p.list_accounts()

        route.respond(500, json={"message": "boom"})
        async with TochkaProvider(TOKEN, sleep=no_sleep) as p:
            with pytest.raises(BankServerError, match="временно не отвечает"):
                await p.list_accounts()
        assert route.call_count == 2 + 3  # 403 без повторов, 500 — ещё 2 повтора

        route.side_effect = httpx.ConnectError(f"boom {TOKEN}")
        async with TochkaProvider(TOKEN, sleep=no_sleep) as p:
            with pytest.raises(BankNetworkError, match="Нет связи с банком"):
                await p.list_accounts()

        mock.get(f"{TBANK}/api/v4/bank-accounts").respond(200, json=[])
        async with TBankProvider(TB_TOKEN, sleep=no_sleep) as p:
            with pytest.raises(BankAPIError, match="рублёвых счетов не видно"):
                await p.check_access()
    assert TOKEN not in caplog.text and TB_TOKEN not in caplog.text
    assert caplog.records


# ---------- подключение ----------


API = "/api/v1/bank/connections"


async def test_connect_verify_replace_disconnect_and_token_never_returned_or_logged(client, tokens, caplog, no_celery):
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []

    async def req(method, url, expect, **kw):
        r = await client.request(method, url, headers=auth(tokens), **kw)
        bodies.append(r.text)
        assert r.status_code == expect, r.text
        return r.json() if r.content else None

    with respx.mock(assert_all_called=False) as mock:
        accounts = mock.get(f"{TOCHKA}/accounts")
        accounts.respond(401, json={"message": f"bad token {TOKEN}"})
        err = await req("POST", API, 400, json={"provider": "tochka", "token": TOKEN})
        assert err["detail"].startswith("Банк не подключён: Точка не приняла токен")
        assert await req("GET", API, 200) == []  # не принятый банком токен не сохраняем

        accounts.respond(json=tochka_accounts(OUR))
        saved = await req("POST", API, 201, json={"provider": "tochka", "token": f"  {TOKEN}  "})
        conn = saved["connection"]
        assert (saved["replaced"], saved["sync_queued"]) == (False, True)
        assert (conn["title"], conn["status"], conn["accounts"]) == ("Точка", "active", [{"number": "···2345", "name": None}])
        assert no_celery[-1][0] == "bank"  # загрузка истории — сразу в очередь
        # новый токен к тем же счетам — заменяет прежний, а не второе подключение
        again = await req("POST", API, 201, json={"provider": "tochka", "token": TOKEN + "-new"})
        assert again["replaced"] is True and again["connection"]["id"] == conn["id"]
        assert len(await req("GET", API, 200)) == 1

        ok = await req("POST", f"{API}/{conn['id']}/verify", 200)
        assert ok["ok"] is True and ok["message"] == "Точка: доступ есть, счетов — 1"
        accounts.respond(401, json={"message": f"expired {TOKEN}-new"})  # даже если банк вернёт токен в ответе
        bad = await req("POST", f"{API}/{conn['id']}/verify", 200)
        assert bad["ok"] is False and bad["connection"]["status"] == "auth"
        too_long = await req("POST", API, 400, json={"provider": "tochka", "token": TOKEN * 400})
        assert "8000" in too_long["detail"]  # проверка формы — без обращения к банку
        await req("POST", API, 400, json={"provider": "sber", "token": TOKEN})  # банк не поддерживается
        await req("DELETE", f"{API}/{conn['id']}", 204)
        assert await req("GET", API, 200) == []

    for b in bodies:
        assert TOKEN not in b
    assert TOKEN not in caplog.text
    assert caplog.records


# ---------- обновление ----------


async def setup_planning(client, tokens):
    body = {"supplier_payment": "upfront", "accounts": [{"name": "Расчётный счёт", "kind": "checking"}],
            "ozon_account": 0, "categories": ["rent", "taxes"]}
    await call(client, tokens, "POST", "/setup", json=body)


async def connect(client, tokens, mock, provider="tochka", token=TOKEN):
    r = await client.post(API, json={"provider": provider, "token": token}, headers=auth(tokens))
    assert r.status_code == 201, r.text
    return r.json()["connection"]


async def run_bank(sessions, uid, queued):
    """Обновление банка через общий runner: запуск, поставленный API в очередь (подключение, «Обновить»), или новый."""
    pending = [q for q in queued if q[0] == "bank"]
    for q in pending:
        queued.remove(q)
    run_id = pending[-1][2] if pending else None
    return await run_sync("bank", uid, sessions, run_id, pending[-1][3] if pending else None)


async def test_sync_history_dedup_ozon_transfer_and_status(client, tokens, sessions, no_celery):
    await setup_planning(client, tokens)
    uid = await user_id(client, tokens)
    await add_days(sessions, uid, {date(2026, 9, 30): D(50000)}, {date(2026, 9, 28): D(7000)}, TODAY)
    out = tx("t-out", "2026-10-03", 5000, "Debit", "7700000000", "Своя компания", SECOND, "Перевод на второй счёт", "301")
    into = tx("t-in", "2026-10-03", 5000, "Credit", "7700000000", "Своя компания", OUR, "Перевод на второй счёт", "301")
    with respx.mock(assert_all_called=False) as mock:
        tochka(mock, {OUR: [OZON_TX, RENT_TX, out, PENDING], SECOND: [into]}, {OUR: 115000, SECOND: 5000})
        conn = await connect(client, tokens, mock)
        run = await run_bank(sessions, uid, no_celery)
        assert run.status == "success", run.error
        assert run.stats["new"] == 4 and run.stats["ozon"] == 1 and run.stats["transfer"] == 2  # Pending — не берём
        # второе обновление — те же операции (с 3 днями запаса назад): дублей нет
        run = await run_bank(sessions, uid, no_celery)
        assert (run.stats["new"], run.stats["duplicate"]) == (0, 4)

    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-09-01"}))["items"]
    ozon = [o for o in ops if o["counterparty"] == "Ozon"]
    assert [(o["day"], o["amount"], o["source"]) for o in ozon] == [("2026-10-02", "50000.00", "bank")]  # одна операция
    transfer = [o for o in ops if o["transfer"]]
    assert len(transfer) == 1 and transfer[0]["transfer"]["account"]["name"] == "Банк Точка ···7890"
    accounts = {a["name"]: a for a in await call(client, tokens, "GET", "/accounts")}
    main = accounts["Банк Точка ···2345"]
    assert (main["source"], main["opening"]) == ("bank", {"amount": "100000.00", "as_of": "2025-10-14"})
    assert main["balance"] == "115000.00"  # 100 000 + 50 000 − 30 000 − 5 000
    async with sessions() as db:
        saved = await db.get(BankConnection, uuid.UUID(conn["id"]))
        assert saved.state == {OUR: {"synced_to": "2026-10-14"}, SECOND: {"synced_to": "2026-10-14"}}
        assert TOKEN not in saved.token_encrypted  # только шифротекст
    status = (await client.get("/api/v1/sync/status", headers=auth(tokens))).json()
    assert status["has_bank"] is True and status["bank"]["last_run"]["status"] == "success"
    listed = (await client.get(API, headers=auth(tokens))).json()[0]
    assert listed["last_sync_at"] is not None and listed["status"] == "active"


async def test_file_and_api_same_operation_is_not_duplicated(client, tokens, sessions, no_celery):
    await setup_planning(client, tokens)
    uid = await user_id(client, tokens)
    rent_file = doc(7, "05.10.2026", "30000.00", OUR, "40802810500000077777", receiver_inn="500100732259",
                    receiver_name="ИП Петров Петр Петрович", purpose="Аренда склада за октябрь")
    await send(client, tokens, statement([rent_file]), dry_run=False)
    with respx.mock(assert_all_called=False) as mock:
        tochka(mock, {OUR: [RENT_TX, TAX_TX]}, {OUR: 61000}, start=None)
        await connect(client, tokens, mock)
        run = await run_bank(sessions, uid, no_celery)
    assert (run.stats["new"], run.stats["duplicate"]) == (1, 1)  # аренда — уже из файла, новый только налог
    ops = (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01"}))["items"]
    assert sorted(o["amount"] for o in ops) == ["-30000.00", "-9000.00"]
    tax = next(o for o in ops if o["amount"] == "-9000.00")
    assert (tax["category"]["name"], tax["category_by"]) == ("Налоги и взносы", "auto")  # встроенное правило


async def test_auth_error_marks_connection_and_scheduled_sync_skips_it(client, tokens, sessions, no_celery):
    await setup_planning(client, tokens)
    uid = await user_id(client, tokens)
    with respx.mock(assert_all_called=False) as mock:
        tochka(mock, {OUR: [RENT_TX]}, {OUR: 1})
        conn = await connect(client, tokens, mock)
        mock.get(f"{TOCHKA}/accounts").respond(401, json={"message": "expired"})
        run = await run_bank(sessions, uid, no_celery)
    assert run.status == "failed" and run.error.startswith("Точка не приняла токен")
    listed = (await client.get(API, headers=auth(tokens))).json()[0]
    assert (listed["id"], listed["status"]) == (conn["id"], "auth")
    async with sessions() as db:
        assert await bank_connections.users_with_connections(db) == []  # по расписанию не дёргаем — ждём новый токен
    r = await client.post(f"{API}/sync", headers=auth(tokens))
    assert r.status_code == 202  # «Обновить» вручную можно — вдруг токен уже исправили


async def test_enp_payment_closes_planned_tax(client, tokens, sessions):
    uid = await user_id(client, tokens)
    await add_sales(sessions, uid, days(date(2026, 7, 1), date(2026, 9, 30)), "1000")  # аванс УСН — 5 520 ₽ до 28.10
    await call(client, tokens, "PUT", "", base="/api/v1/tax-profile", json=USN)
    await setup_planning(client, tokens)
    # ЕНП 9 000 ₽ за 18 дней до срока: сумма не совпадает с планом (оценка), но это тот же налог
    await send(client, tokens, statement([doc(15, "10.10.2026", "9000.00", OUR, TREASURY, receiver_inn="7727406020",
                                              receiver_name="Казначейство России (ФНС России)",
                                              purpose="Единый налоговый платеж")]), dry_run=False)
    body = await call(client, tokens, "GET", "/calendar", params={"date_from": "2026-10-14", "date_to": "2026-11-30"})
    [tax] = [ln for ln in body["lines"] if ln["source"] == "tax"]
    assert (tax["title"], tax["status"], tax["fact"]["amount"], tax["fact"]["day"]) == (
        "Аванс УСН за 9 месяцев", "paid", "-9000.00", "2026-10-10")
    block = await call(client, tokens, "GET", "/upcoming", base="/api/v1/tax-profile")
    assert block["items"][0]["status"] == "paid"  # и в «Ближайших сроках» налогов
