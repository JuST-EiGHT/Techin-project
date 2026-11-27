"""Реферальная программа, шаг 11.7: участники (кто оставил отзыв), личные ссылки с кодом в utm_content, «Отправлено»,
статусы, удаление и возврат, Excel, события ленты, магазины не пересекаются."""
import io
from datetime import timedelta

import pytest
from openpyxl import load_workbook

from app.engine import referral as eng
from app.engine import traffic_links as tl
from app.services.sync.finance import today_msk
from tests.test_traffic_links import PREFIX, auth, me, query, seed_products

API = "/api/v1/referral"
SHOP = "https://www.ozon.ru/seller/myshop/"


# ── чистый модуль ──


def test_codes_are_unique_and_readable():
    taken: set[str] = set()
    for _ in range(300):
        c = eng.new_code(taken)
        assert len(c) == 6 and set(c) <= set(eng.CODE_ALPHABET) and c not in taken
        taken.add(c)
    assert not set("01ilo") & set(eng.CODE_ALPHABET)  # путаные символы — не используем


def test_code_from_report_content():
    assert eng.code_of("ref_ab3k7m") == "ab3k7m" and eng.code_of(" REF_AB3K7M ") == "ab3k7m"
    assert eng.code_of("link_in_bio") is None and eng.code_of("") is None and eng.code_of(None) is None


def test_program_settings_checked():
    p = eng.check_program(" Friends ", "referral", "friend", "ozon.ru/seller/myshop/?at=x")
    assert (p.campaign_name, p.store_url) == ("friends", SHOP)  # служебный «Поделиться» — убран
    with pytest.raises(tl.LinkError):
        eng.check_program("друзья", "referral", "friend", None)
    with pytest.raises(eng.ReferralError, match="utm_source и utm_medium"):
        eng.check_program("friends", "", "friend", None)
    with pytest.raises(eng.ReferralError, match="страница магазина"):
        eng.check_program("friends", "referral", "friend", "https://www.ozon.ru/product/1/")


def test_sent_state_by_last_event():
    assert eng.sent_state([("created", 1)]) == (False, None)
    assert eng.sent_state([("created", 1), ("sent", 2)]) == (True, 2)
    assert eng.sent_state([("sent", 2), ("unsent", 3)]) == (False, None)
    assert eng.sent_state([("sent", 2), ("unsent", 3), ("sent", 4)]) == (True, 4)


# ── API: сценарий владельца ──


async def setup(client, tokens, sessions):
    ids = await seed_products(sessions, await me(client, tokens))
    await client.put("/api/v1/traffic/settings", json={"utm_prefix": PREFIX}, headers=auth(tokens))
    return ids


async def test_needs_prefix_first(client, tokens, sessions):
    ids = await seed_products(sessions, await me(client, tokens))
    s = (await client.get(f"{API}/settings", headers=auth(tokens))).json()
    assert s["utm_prefix"] is None and s["campaign"] is None and s["program"]["defaults"] is True
    r = await client.post(f"{API}/participants", json={"name": "Анна К.", "product_id": str(ids["A"])}, headers=auth(tokens))
    assert r.status_code == 400 and "префикс" in r.json()["detail"]


async def test_participant_gets_personal_link_and_is_marked_sent(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    s = (await client.get(f"{API}/settings", headers=auth(tokens))).json()
    assert s["campaign"] == f"{PREFIX}_friends" and s["program"]["utm_source"] == "referral"
    assert set(s["channels"]) == {"ozon_chat", "outside", "review", "other"}

    day = str(today_msk() - timedelta(days=2))
    r = await client.post(f"{API}/participants", json={"name": "  Анна   К. ", "product_id": str(ids["A"]),
                                                      "review_date": day, "note": "5 звёзд"}, headers=auth(tokens))
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["name"], a["status"], a["sent"], a["target"]) == ("Анна К.", "new", None, "product")
    assert a["product"]["offer_id"] == "6001-2RS" and a["review_date"] == day
    q = query(a["url"])
    assert a["url"].startswith("https://www.ozon.ru/product/900001/?")
    assert q == {"utm_source": "referral", "utm_medium": "friend", "utm_campaign": f"{PREFIX}_friends",
                 "utm_content": f"ref_{a['code']}"}
    assert eng.code_of(q["utm_content"]) == a["code"]
    # личная ссылка — в истории ссылок 11.2
    links = (await client.get("/api/v1/traffic/links", headers=auth(tokens))).json()["items"]
    assert [x["link_id"] for x in links] == [a["link_id"]] and links[0]["url"] == a["url"]

    b = (await client.post(f"{API}/participants", json={"name": "Борис", "product_id": str(ids["A"])},
                           headers=auth(tokens))).json()
    assert b["code"] != a["code"] and b["url"] != a["url"]  # у каждого — своя ссылка

    r = await client.post(f"{API}/participants/{a['id']}/sent", json={"channel": "ozon_chat", "note": "ответил в чате"},
                          headers=auth(tokens))
    sent = r.json()
    assert sent["status"] == "sent" and sent["sent"]["channel_title"] == "Чат Ozon"
    assert sent["sent"]["sent_on"] == str(today_msk()) and sent["sent"]["note"] == "ответил в чате"
    r = await client.post(f"{API}/participants/{a['id']}/sent",
                          json={"channel": "outside", "sent_on": str(today_msk() + timedelta(days=1))}, headers=auth(tokens))
    assert r.status_code == 400 and "не позже" in r.json()["detail"]

    data = (await client.get(f"{API}/participants", headers=auth(tokens))).json()
    assert data["counts"] == {"all": 2, "new": 1, "sent": 1, "deleted": 0}
    assert [x["name"] for x in data["items"]] == ["Борис", "Анна К."]  # новые сверху
    assert [x["name"] for x in (await client.get(f"{API}/participants", params={"tab": "new"},
                                                 headers=auth(tokens))).json()["items"]] == ["Борис"]
    assert (await client.get(f"{API}/participants", params={"channel": "ozon_chat"}, headers=auth(tokens))).json()["total"] == 1
    assert (await client.get(f"{API}/participants", params={"search": a["code"]}, headers=auth(tokens))).json()["total"] == 1
    assert (await client.get(f"{API}/participants", params={"search": "6001"}, headers=auth(tokens))).json()["total"] == 2

    back = (await client.post(f"{API}/participants/{a['id']}/unsent", headers=auth(tokens))).json()
    assert back["status"] == "new" and back["sent"] is None
    titles = [e["title"] for e in (await client.get(f"{API}/participants/{a['id']}/events", headers=auth(tokens))).json()]
    assert titles == ["Отметка «отправлено» снята", "Ссылка отправлена", "Добавлен, ссылка создана"]

    r = await client.patch(f"{API}/participants/{a['id']}", json={"name": "Анна Ковалёва", "note": ""}, headers=auth(tokens))
    assert (r.json()["name"], r.json()["note"], r.json()["url"]) == ("Анна Ковалёва", None, a["url"])  # ссылка та же


async def test_store_link_settings_and_errors(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    r = await client.post(f"{API}/participants", json={"name": "Вера", "target": "store"}, headers=auth(tokens))
    assert r.status_code == 400 and "ссылку на магазин" in r.json()["detail"]
    r = await client.post(f"{API}/participants", json={"name": "Вера"}, headers=auth(tokens))
    assert r.status_code == 400 and "Выберите товар" in r.json()["detail"]
    r = await client.post(f"{API}/participants", json={"name": "Вера", "product_id": str(ids["B"])}, headers=auth(tokens))
    assert r.status_code == 400 and "нет SKU" in r.json()["detail"]
    r = await client.post(f"{API}/participants", json={"name": " ", "product_id": str(ids["A"])}, headers=auth(tokens))
    assert r.status_code == 400 and "имя" in r.json()["detail"]

    r = await client.put(f"{API}/settings", json={"campaign_name": "otzyv", "utm_source": "chat", "utm_medium": "dm",
                                                  "store_url": SHOP}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["campaign"] == f"{PREFIX}_otzyv"
    v = (await client.post(f"{API}/participants", json={"name": "Вера", "target": "store", "product_id": str(ids["A"])},
                           headers=auth(tokens))).json()
    assert v["url"].startswith(SHOP + "?") and query(v["url"])["utm_campaign"] == f"{PREFIX}_otzyv"
    assert v["product"]["offer_id"] == "6001-2RS"  # товар из отзыва — для учёта, ссылка — на магазин


async def test_delete_restore_export_and_stores(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    a = (await client.post(f"{API}/participants", json={"name": "Анна", "product_id": str(ids["A"])},
                           headers=auth(tokens))).json()
    assert (await client.delete(f"{API}/participants/{a['id']}", headers=auth(tokens))).status_code == 204
    data = (await client.get(f"{API}/participants", headers=auth(tokens))).json()
    assert data["total"] == 0 and data["counts"]["deleted"] == 1
    r = await client.post(f"{API}/participants/{a['id']}/sent", json={"channel": "other"}, headers=auth(tokens))
    assert r.status_code == 400 and "верните" in r.json()["detail"]
    assert (await client.post(f"{API}/participants/{a['id']}/restore", headers=auth(tokens))).json()["deleted"] is False
    # ссылку убранного участника не трогаем — её уже могли отправить
    assert (await client.get("/api/v1/traffic/links", headers=auth(tokens))).json()["total"] == 1

    r = await client.get(f"{API}/participants/export", headers=auth(tokens))
    ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
    assert ws.cell(1, 2).value == "Участник" and ws.cell(2, 2).value == "Анна" and ws.cell(2, 3).value == a["code"]

    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    assert (await client.get(f"{API}/participants", headers=auth(other))).json()["total"] == 0
    assert (await client.post(f"{API}/participants/{a['id']}/sent", json={"channel": "other"},
                              headers=auth(other))).status_code == 404
    await client.put("/api/v1/traffic/settings", json={"utm_prefix": "vendor_org_999"}, headers=auth(other))
    r = await client.post(f"{API}/participants", json={"name": "Чужой товар", "product_id": str(ids["A"])},
                          headers=auth(other))
    assert r.status_code == 400 and "не найден" in r.json()["detail"]


async def test_actions_in_event_feed(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    a = (await client.post(f"{API}/participants", json={"name": "Анна", "product_id": str(ids["A"])},
                           headers=auth(tokens))).json()
    await client.post(f"{API}/participants/{a['id']}/sent", json={"channel": "ozon_chat"}, headers=auth(tokens))
    r = await client.get("/api/v1/analytics/events", params={"kind": "action", "limit": 50}, headers=auth(tokens))
    events = {e["title"]: e for e in r.json()["items"]}
    assert "Добавлен участник реферальной программы — создана личная ссылка" in events
    sent = events["Личная ссылка участнику отмечена отправленной"]
    assert sent["link"] == f"/dashboard/advertising/referral?participant={a['id']}"
