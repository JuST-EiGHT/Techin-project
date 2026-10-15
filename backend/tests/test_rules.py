"""Правила разнесения (шаг 3.12): условия и приоритет, встроенные правила (маркетплейс, перевод, налог), ручная разметка
важнее правил и не теряется, пересчёт — только неразмеченных, удаление правила, предпросмотр, массовая разметка,
фильтр «без статьи», предложение «правило для контрагента», правила при загрузке файлом (3.8) и вручную."""
from decimal import Decimal

from app.engine import categorize as eng
from tests.test_bank import CLIENT, OUR, RENT, doc, send, statement
from tests.test_plan_calendar import call, fixed_today  # noqa: F401 — фикстура «сегодня»
from tests.test_planning import upload

D = Decimal
SUBSCRIPTION = doc(31, "08.10.2026", "990.00", OUR, "40702810800000033333", receiver_inn="7704340310",
                   receiver_name='ООО "ЯНДЕКС"', purpose="Оплата подписки Яндекс 360 за октябрь")
TAX = doc(15, "10.10.2026", "9000.00", OUR, "03100643000000018500", receiver_inn="7727406020",
          receiver_name="Казначейство России (ФНС России)", purpose="Единый налоговый платеж")


def test_engine_conditions_priority_and_tax():
    rent = eng.Rule("1", "out", "rent", priority=10, purpose="аренд, arenda")
    petrov = eng.Rule("2", "out", "other", priority=20, counterparty="Петров ИП")
    big = eng.Rule("3", "out", "big", priority=30, amount_min=D(10000), amount_max=D(50000))
    f = eng.Facts(D("-30000"), "ИП Петров Петр Петрович", "500100732259", "Аренда склада за октябрь")
    assert [eng.fits(r, f) for r in (rent, petrov, big)] == [True, True, True]
    assert eng.pick([big, petrov, rent], f).id == "1"  # первое по порядку
    assert not eng.fits(rent, eng.Facts(D("30000"), purpose="аренда"))  # направление: это поступление
    assert not eng.fits(eng.Rule("4", "out", "x", counterparty_inn="7701234567"), f)
    assert not eng.fits(big, eng.Facts(D("-9000")))
    assert not eng.fits(eng.Rule("5", "out", "x"), f)  # без условий правило не срабатывает
    assert eng.norm("ООО «Ёлка-Палка»") == "ооо елка палка"
    assert eng.is_tax(eng.Facts(D("-1"), "Казначейство России (ФНС России)", "7727406020"))
    assert eng.is_tax(eng.Facts(D("-1"), "УФК по г. Москве", counterparty_account="03100643000000017300"))
    assert not eng.is_tax(eng.Facts(D("1"), inn="7727406020"))  # возврат из бюджета — не платёж налога
    assert not eng.is_tax(eng.Facts(D("-1"), "ИП Петров", "500100732259"))


async def setup(client, tokens):
    body = {"supplier_payment": "upfront", "accounts": [{"name": "Расчётный счёт", "kind": "checking"}],
            "ozon_account": 0, "categories": ["rent", "subscriptions", "other_expense", "other_income", "taxes"]}
    await call(client, tokens, "POST", "/setup", json=body)
    return {c["code"]: c["id"] for c in await call(client, tokens, "GET", "/categories") if c["code"]}


async def ops(client, tokens, **params):
    return (await call(client, tokens, "GET", "/operations", params={"date_from": "2026-10-01", **params}))["items"]


def by_amount(items):
    return {o["amount"]: o for o in items}


async def test_rules_priority_manual_marking_and_recalc(client, tokens):
    cats = await setup(client, tokens)
    await send(client, tokens, statement([RENT, CLIENT, SUBSCRIPTION, TAX]), dry_run=False)
    items = by_amount(await ops(client, tokens))
    assert items["-9000.00"]["category"]["name"] == "Налоги и взносы"  # встроенное: казначейство
    assert {o["amount"] for o in await ops(client, tokens, uncategorized=True)} == {"-30000.00", "1500.50", "-990.00"}

    # предпросмотр — до сохранения
    preview = await call(client, tokens, "POST", "/rules/preview", json={
        "direction": "out", "purpose": "аренд", "category_id": cats["rent"]})
    assert preview["matched"] == 1 and preview["examples"][0]["amount"] == "-30000.00"

    rent = await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "purpose": "аренд", "category_id": cats["rent"]})
    assert rent["applied"] == 1 and rent["rule"]["operations"] == 1
    petrov = await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "counterparty": "Петров", "category_id": cats["other_expense"]})
    assert petrov["applied"] == 0  # аренду уже разнесло правило выше
    items = by_amount(await ops(client, tokens))
    assert (items["-30000.00"]["category"]["name"], items["-30000.00"]["category_by"]) == ("Аренда", "rule")

    # порядок: «Петров» выше «аренды» — операция переходит к нему
    page = await call(client, tokens, "POST", "/rules/reorder", json={"ids": [petrov["rule"]["id"], rent["rule"]["id"]]})
    assert [r["id"] for r in page["rules"]] == [petrov["rule"]["id"], rent["rule"]["id"]]
    assert [b["code"] for b in page["builtin"]] == ["marketplaces", "transfers", "taxes"]
    assert by_amount(await ops(client, tokens))["-30000.00"]["category"]["name"] == "Прочие расходы"

    # ручная разметка важнее правил и не перезаписывается
    sub = by_amount(await ops(client, tokens))["-990.00"]
    saved = await call(client, tokens, "PATCH", f"/operations/{sub['op_id']}", json={"category_id": cats["subscriptions"]})
    suggestion = saved["rule_suggestion"]
    assert (suggestion["counterparty_inn"], suggestion["category"]["name"], suggestion["others"]) == (
        "7704340310", "Подписки и сервисы", 0)
    await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "purpose": "подписк", "category_id": cats["other_expense"]})
    assert by_amount(await ops(client, tokens))["-990.00"]["category"]["name"] == "Подписки и сервисы"
    rent_op = by_amount(await ops(client, tokens))["-30000.00"]
    await call(client, tokens, "PATCH", f"/operations/{rent_op['op_id']}", json={"category_id": cats["rent"]})
    await call(client, tokens, "DELETE", f"/rules/{petrov['rule']['id']}")
    assert by_amount(await ops(client, tokens))["-30000.00"]["category_by"] == "user"

    # удалили правило — его операции получают следующее подошедшее или остаются без статьи
    deleted = await call(client, tokens, "DELETE", f"/rules/{rent['rule']['id']}")
    assert deleted["applied"] == 0  # аренда размечена вручную — не трогаем
    history = await call(client, tokens, "GET", f"/operations/{rent_op['op_id']}/history")
    assert [h["category"] for h in history[:2]] == ["Аренда", "Прочие расходы"]  # каждая смена статьи — версия


async def test_rule_validation_bulk_marking_and_new_operations(client, tokens):
    cats = await setup(client, tokens)
    errors = [
        ({"direction": "in", "purpose": "x", "category_id": cats["rent"]}, "статью поступлений"),
        ({"direction": "out", "category_id": cats["rent"]}, "хотя бы одно условие"),
        ({"direction": "out", "counterparty_inn": "123", "category_id": cats["rent"]}, "ИНН — 10 цифр"),
        ({"direction": "out", "amount_min": "500", "amount_max": "100", "category_id": cats["rent"]}, "«от» больше"),
    ]
    for body, text in errors:
        r = await call(client, tokens, "POST", "/rules", 400, json=body)
        assert text in r["detail"], r
    await send(client, tokens, statement([RENT, CLIENT, SUBSCRIPTION]), dry_run=False)
    items = by_amount(await ops(client, tokens))

    # массовая разметка: поступление и платёж вместе — нельзя одной статьёй
    mixed = await call(client, tokens, "POST", "/operations/categorize", 400, json={
        "op_ids": [items["-30000.00"]["op_id"], items["1500.50"]["op_id"]], "category_id": cats["other_expense"]})
    assert "только поступления или только платежи" in mixed["detail"]
    done = await call(client, tokens, "POST", "/operations/categorize", json={
        "op_ids": [items["-30000.00"]["op_id"], items["-990.00"]["op_id"]], "category_id": cats["other_expense"]})
    assert done["changed"] == 2
    assert {o["amount"] for o in await ops(client, tokens, uncategorized=True)} == {"1500.50"}

    # правило по ИНН — и для новых операций: вручную без статьи и загрузкой файлом (3.8)
    await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "in", "counterparty_inn": "7701234567", "category_id": cats["other_income"]})
    assert by_amount(await ops(client, tokens))["1500.50"]["category"]["name"] == "Прочие поступления"
    await call(client, tokens, "POST", "/rules", 201, json={
        "direction": "out", "counterparty": "яндекс", "amount_max": "5000", "category_id": cats["subscriptions"]})
    accounts = {a["name"]: a["id"] for a in await call(client, tokens, "GET", "/accounts")}
    manual = await call(client, tokens, "POST", "/operations", 201, json={
        "type": "expense", "amount": "299", "day": "2026-10-12", "account_id": accounts["Расчётный счёт"],
        "counterparty": "Яндекс Плюс"})
    assert manual["category"]["name"] == "Подписки и сервисы"
    csv = "Дата;Расход, руб.;Счёт;Контрагент;Описание\n13.10.2026;450;Расчётный счёт;ООО Яндекс;Яндекс Диск\n"
    result = await upload(client, tokens, csv.encode("utf-8"), name="ops.csv", apply=True)
    assert result["counts"]["new"] == 1
    imported = by_amount(await ops(client, tokens))["-450.00"]
    assert (imported["category"]["name"], imported["category_by"], imported["source"]) == (
        "Подписки и сервисы", "rule", "import")
    # правило выключили — его операции без ручной разметки остаются без статьи
    rules = (await call(client, tokens, "GET", "/rules"))["rules"]
    yandex = next(r for r in rules if r["counterparty"] == "яндекс")
    assert yandex["operations"] == 2
    saved = await call(client, tokens, "PATCH", f"/rules/{yandex['id']}", json={"enabled": False})
    assert saved["applied"] == 2 and saved["rule"]["enabled"] is False
    assert {o["amount"] for o in await ops(client, tokens, uncategorized=True)} == {"-299.00", "-450.00"}
