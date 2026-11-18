"""«Внешний трафик», шаг 11.2: история ссылок — сохранение, версии, удаление и возврат, фильтры, диплинк и подменный
артикул из кабинета Ozon, срок диплинка и уведомление о нём."""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.engine import anomaly_detector as det
from app.engine import traffic_links as tl
from app.models.anomaly import Anomaly
from app.models.traffic import TrafficLink
from app.services import anomalies, traffic_history
from tests.test_traffic_links import PREFIX, auth, me, query, seed_products

API = "/api/v1/traffic"
DEEPLINK = "https://ozon.onelink.me/AbCd/x1y2z3"


# ── чистый модуль ──


@pytest.mark.parametrize("text, value", [
    ("O-4374100", "O-4374100"), ("о-4374100", "O-4374100"), ("О4374100", "O-4374100"), ("0-4374100", "O-4374100"),
    (" o-437 4100 ", "O-4374100"), ("", None),
])
def test_substitute_code(text, value):
    assert tl.check_substitute(text) == value


def test_substitute_code_errors():
    for bad in ("A-4374100", "O-", "4374100", "O-12ab"):
        with pytest.raises(tl.LinkError, match="O-4374100"):
            tl.check_substitute(bad)


def test_deeplink_check():
    assert tl.check_deeplink("ozon.onelink.me/AbCd/x1y2z3") == DEEPLINK
    assert tl.check_deeplink("  ") is None
    for bad in ("https://www.ozon.ru/product/1/", "https://evil.com/onelink.me/a/b", "https://ozon.onelink.me/"):
        with pytest.raises(tl.LinkError):
            tl.check_deeplink(bad)
    with pytest.raises(tl.LinkError, match="макросами"):
        tl.check_deeplink(DEEPLINK, tl.Labels(campaign=f"{PREFIX}_x", source="vk", medium="cpc", content="{{age}}"))


def test_deeplink_state():
    since = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
    s = tl.deeplink_state(since, [], since + timedelta(days=10))
    assert (s.state, s.days_left, s.expires_at.date().isoformat()) == ("ok", 20, "2026-12-01")
    assert tl.deeplink_state(since, [], since + timedelta(days=24)).state == "soon"  # 6 дней — за неделю предупреждаем
    gone = tl.deeplink_state(since, [], since + timedelta(days=31))
    assert gone.state == "expired" and gone.days_left < 0
    opened = tl.deeplink_state(since, [since + timedelta(days=25)], since + timedelta(days=31))
    assert opened.state == "ok" and opened.days_left == 24  # переход — отсчёт заново


def test_deeplink_anomaly():
    lid = uuid.uuid4()
    item = lambda state, left: det.DeeplinkItem(lid, None, "Мочалка", "vk", state, datetime(2026, 12, 1).date(), left)  # noqa: E731
    assert det.deeplink_expiring([item("ok", 20)]).findings == []
    [soon] = det.deeplink_expiring([item("soon", 5)]).findings
    assert (soon.severity, soon.key) == (det.YELLOW, f"link:{lid}")
    assert det.deeplink_expiring([item("expired", -1)]).findings[0].severity == det.RED


# ── API ──


async def setup(client, tokens, sessions):
    ids = await seed_products(sessions, await me(client, tokens))
    await client.put(f"{API}/settings", json={"utm_prefix": PREFIX}, headers=auth(tokens))
    return ids


def body(ids, **over):
    return {"product_ids": [str(ids["A"])], "urls": "https://www.ozon.ru/seller/myshop/", "channel": "social",
            "campaign": "autumn", "source": "inst", "medium": "story", **over}


async def test_build_saves_links_without_duplicates(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    r = await client.post(f"{API}/links", json={**body(ids), "urls": "https://www.ozon.ru/seller/myshop/\nhttps://x.ru/"},
                          headers=auth(tokens))
    assert r.status_code == 201, r.text
    rows = r.json()["rows"]
    assert rows[0]["link_id"] and rows[1]["link_id"] and rows[2]["link_id"] is None  # ошибка не сохраняется
    again = (await client.post(f"{API}/links", json=body(ids), headers=auth(tokens))).json()["rows"]
    assert [x["link_id"] for x in again] == [rows[0]["link_id"], rows[1]["link_id"]]  # та же ссылка — не дубль

    listed = (await client.get(f"{API}/links", headers=auth(tokens))).json()
    assert listed["total"] == 2 and listed["counts"]["all"] == 2
    shop = next(x for x in listed["items"] if x["section"] == "seller")
    assert shop["source_url"] == "https://www.ozon.ru/seller/myshop/" and shop["campaign_name"] == "autumn"
    assert (shop["utm_source"], shop["utm_medium"], shop["version"], shop["versions"]) == ("inst", "story", 1, 1)
    product = next(x for x in listed["items"] if x["section"] == "product")
    assert product["product"]["offer_id"] == "6001-2RS"
    assert listed["facets"] == {"campaigns": ["autumn"], "sources": ["inst"], "mediums": ["story"]}

    # другой магазин ссылок не видит
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    assert (await client.get(f"{API}/links", headers=auth(other))).json()["total"] == 0
    assert (await client.patch(f"{API}/links/{shop['link_id']}", json={"medium": "x"}, headers=auth(other))).status_code == 404


async def test_edit_is_new_version_and_history_kept(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    lid = (await client.post(f"{API}/links", json=body(ids, product_ids=[]), headers=auth(tokens))).json()["rows"][0]["link_id"]
    r = await client.patch(f"{API}/links/{lid}", json={"medium": "reels", "content": "bio"}, headers=auth(tokens))
    assert r.status_code == 200, r.text
    link = r.json()["link"]
    assert (link["version"], link["versions"], link["utm_medium"]) == (2, 2, "reels")
    assert query(link["url"])["utm_content"] == "bio"
    # ничего не изменилось — новой версии нет
    assert (await client.patch(f"{API}/links/{lid}", json={"medium": "reels"}, headers=auth(tokens))).json()["link"]["version"] == 2
    r = await client.patch(f"{API}/links/{lid}", json={"medium": ""}, headers=auth(tokens))
    assert r.status_code == 400 and "utm_medium" in r.json()["detail"]
    vers = (await client.get(f"{API}/links/{lid}/versions", headers=auth(tokens))).json()
    assert [v["version"] for v in vers] == [2, 1] and vers[1]["utm_medium"] == "story" and vers[1]["superseded_at"]
    async with sessions() as db:
        assert len((await db.scalars(select(TrafficLink).where(TrafficLink.link_id == uuid.UUID(lid)))).all()) == 2


async def test_deeplink_substitute_and_label_change(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    lid = (await client.post(f"{API}/links", json=body(ids, urls=""), headers=auth(tokens))).json()["rows"][0]["link_id"]
    r = await client.patch(f"{API}/links/{lid}", json={"deeplink": "https://www.ozon.ru/product/1/"}, headers=auth(tokens))
    assert r.status_code == 400 and "onelink.me" in r.json()["detail"]
    r = await client.patch(f"{API}/links/{lid}", json={"deeplink": DEEPLINK, "substitute_code": "о-4374100"},
                           headers=auth(tokens))
    link = r.json()["link"]
    assert (link["deeplink"], link["substitute_code"]) == (DEEPLINK, "O-4374100")
    assert link["deeplink_status"]["state"] == "ok" and link["deeplink_status"]["days_left"] == 30
    tabs = (await client.get(f"{API}/links", headers=auth(tokens))).json()["counts"]
    assert (tabs["deeplink"], tabs["substitute"], tabs["expiring"]) == (1, 1, 0)

    # метки поменялись — диплинк сделан для прежних, убираем и предупреждаем
    r = await client.patch(f"{API}/links/{lid}", json={"campaign": "winter"}, headers=auth(tokens))
    saved = r.json()
    assert saved["link"]["deeplink"] is None and "Диплинк" in saved["warnings"][0]
    assert saved["link"]["substitute_code"] == "O-4374100"
    r = await client.patch(f"{API}/links/{lid}", json={"content": "{{age}}", "deeplink": DEEPLINK}, headers=auth(tokens))
    assert r.status_code == 400 and "макросами" in r.json()["detail"]
    r = await client.patch(f"{API}/links/{lid}", json={"substitute_code": ""}, headers=auth(tokens))
    assert r.json()["link"]["substitute_code"] is None


async def test_delete_restore_and_filters(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    rows = (await client.post(f"{API}/links", json=body(ids), headers=auth(tokens))).json()["rows"]
    await client.post(f"{API}/links", json=body(ids, product_ids=[], urls="https://www.ozon.ru/brand/nachi-1/",
                                                 campaign="vk_ads", channel="vk", source=None, medium="cpc"),
                      headers=auth(tokens))
    get = lambda **p: client.get(f"{API}/links", params=p, headers=auth(tokens))  # noqa: E731
    assert (await get(campaign="vk_ads")).json()["total"] == 1
    assert (await get(source="vk")).json()["items"][0]["section"] == "brand"
    assert (await get(search="6001")).json()["total"] == 1
    assert (await get(search="myshop")).json()["total"] == 1
    today = datetime.now(timezone.utc).date()
    assert (await get(date_from=str(today + timedelta(days=1)))).json()["total"] == 0
    assert (await get(date_from=str(today), date_to=str(today - timedelta(days=1)))).status_code == 400

    lid = rows[0]["link_id"]
    assert (await client.delete(f"{API}/links/{lid}", headers=auth(tokens))).status_code == 204
    data = (await get()).json()
    assert data["total"] == 2 and data["counts"]["deleted"] == 1
    assert (await get(tab="deleted")).json()["items"][0]["link_id"] == lid
    assert (await client.patch(f"{API}/links/{lid}", json={"medium": "x"}, headers=auth(tokens))).status_code == 400
    back = (await client.post(f"{API}/links/{lid}/restore", headers=auth(tokens))).json()
    assert back["deleted"] is False and back["version"] == 2  # возврат — новая версия, удалённая остаётся
    assert (await get()).json()["total"] == 3

    r = await client.get(f"{API}/links/export", headers=auth(tokens))
    ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
    assert ws.cell(1, 1).value == "Создана" and ws.max_row == 4


async def test_deeplink_expiry_open_and_notification(client, tokens, sessions):
    ids = await setup(client, tokens, sessions)
    store = await me(client, tokens)
    lid = (await client.post(f"{API}/links", json=body(ids, urls=""), headers=auth(tokens))).json()["rows"][0]["link_id"]
    r = await client.post(f"{API}/links/{lid}/opened", headers=auth(tokens))
    assert r.status_code == 400 and "бессрочно" in r.json()["detail"]
    await client.patch(f"{API}/links/{lid}", json={"deeplink": DEEPLINK}, headers=auth(tokens))
    async with sessions() as db:  # диплинк добавили 26 дней назад
        row = await db.scalar(select(TrafficLink).where(TrafficLink.link_id == uuid.UUID(lid),
                                                        TrafficLink.superseded_at.is_(None)))
        row.deeplink_since = datetime.now(timezone.utc) - timedelta(days=26)
        await db.commit()
    data = (await client.get(f"{API}/links", params={"tab": "expiring"}, headers=auth(tokens))).json()
    assert data["total"] == 1 and data["items"][0]["deeplink_status"]["state"] == "soon"

    async with sessions() as db:
        await anomalies.detect(db, store)
        [a] = (await db.scalars(select(Anomaly).where(Anomaly.type == det.DEEPLINK_EXPIRING))).all()
        assert a.severity == det.YELLOW and a.resolved_at is None

    r = await client.post(f"{API}/links/{lid}/opened", headers=auth(tokens))
    assert r.json()["deeplink_status"]["state"] == "ok" and r.json()["deeplink_status"]["days_left"] == 30
    async with sessions() as db:
        await anomalies.detect(db, store)
        a = await db.scalar(select(Anomaly).where(Anomaly.type == det.DEEPLINK_EXPIRING))
        assert a.resolved_at is not None  # открыли — уведомление закрылось

    alerts = (await client.get("/api/v1/anomalies", params={"status": "all"}, headers=auth(tokens))).json()
    assert any(x["type"] == det.DEEPLINK_EXPIRING and x["link"].endswith(f"link={lid}") for x in alerts["items"])


async def test_list_is_store_scoped_in_service(sessions):
    async with sessions() as db:
        assert (await traffic_history.list_links(db, uuid.uuid4())).total == 0


# ── шаг 11.6: связи ──


async def test_link_actions_in_event_feed(client, tokens, sessions):
    """Создание, правка, удаление и возврат ссылок — в «Истории изменений и событий»; правка ведёт прямо к ссылке."""
    ids = await setup(client, tokens, sessions)
    lid = (await client.post(f"{API}/links", json=body(ids, urls=""), headers=auth(tokens))).json()["rows"][0]["link_id"]
    await client.patch(f"{API}/links/{lid}", json={"medium": "reels"}, headers=auth(tokens))
    await client.delete(f"{API}/links/{lid}", headers=auth(tokens))
    await client.post(f"{API}/links/{lid}/restore", headers=auth(tokens))
    await client.post(f"{API}/links/{lid}/opened", headers=auth(tokens))  # служебное — не событие
    r = await client.get("/api/v1/analytics/events", params={"kind": "action", "limit": 50}, headers=auth(tokens))
    assert r.status_code == 200, r.text
    events = {e["title"]: e for e in r.json()["items"]}
    assert "Созданы ссылки с UTM" in events and "Удалена ссылка с UTM" in events
    edit = events["Изменена ссылка с UTM: метки, диплинк или подменный артикул"]
    assert edit["link"] == f"/dashboard/advertising/links?tab=history&link={lid}"
    assert events["Возвращена ссылка с UTM"]["link"].endswith(f"link={lid}")
    assert "Изменён префикс для метки UTM_CAMPAIGN" in events
    assert not any("Открыт" in t for t in events)


async def test_product_links_filter(client, tokens, sessions):
    """«Статистика товара» показывает ссылки этого товара (GET /traffic/links?product_id=)."""
    ids = await setup(client, tokens, sessions)
    await client.post(f"{API}/links", json=body(ids), headers=auth(tokens))  # товар A + магазин
    r = await client.get(f"{API}/links", params={"product_id": str(ids["A"])}, headers=auth(tokens))
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["product"]["id"] == str(ids["A"])
    assert (await client.get(f"{API}/links", params={"product_id": str(ids["E"])}, headers=auth(tokens))).json()["total"] == 0
