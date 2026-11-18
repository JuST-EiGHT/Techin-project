"""«Внешний трафик», шаг 11.1: префикс UTM_CAMPAIGN, проверка ссылок Ozon, сборка меток, конструктор, выгрузка, QR."""
import csv
import io
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

import pytest
from openpyxl import load_workbook

from app.engine import traffic_links as tl
from app.models.product import Product

API = "/api/v1/traffic"
PREFIX = "vendor_org_123456"  # условный префикс, не владельца (правило 13)


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


# ── проверка ссылок ──


@pytest.mark.parametrize("text, section, base", [
    ("https://www.ozon.ru/product/podshipnik-6001-123456789/", "product", "https://www.ozon.ru/product/podshipnik-6001-123456789/"),
    ("ozon.ru/product/123456789/", "product", "https://www.ozon.ru/product/123456789/"),
    ("http://OZON.KZ/product/123/", "product", "https://www.ozon.kz/product/123/"),
    ("https://www.ozon.by/product/seller/shop-777/", "seller", "https://www.ozon.by/product/seller/shop-777/"),
    ("https://www.ozon.ru/brand/nachi-100500/", "brand", "https://www.ozon.ru/brand/nachi-100500/"),
    ("https://www.ozon.ru/highlight/rasprodazha-1/", "highlight", "https://www.ozon.ru/highlight/rasprodazha-1/"),
])
def test_allowed_links(text, section, base):
    link = tl.parse_link(text)
    assert (link.section, link.base) == (section, base)


@pytest.mark.parametrize("text, error", [
    ("https://www.wildberries.ru/catalog/1/detail.aspx", "ozon.ru, ozon.kz или ozon.by"),
    ("https://ozon.ru.evil.com/product/1/", "ozon.ru, ozon.kz или ozon.by"),
    ("https://evilozon.ru/product/1/", "ozon.ru, ozon.kz или ozon.by"),
    ("https://user:pass@ozon.ru/product/1/", "ozon.ru, ozon.kz или ozon.by"),
    ("https://www.ozon.ru/category/podshipniki/", "только ссылки на товар"),
    ("https://www.ozon.ru/", "только ссылки на товар"),
    ("https://www.ozon.ru/product/", "только ссылки на товар"),
    ("https://ozon.ru/t/AbCdEf", "только ссылки на товар"),
    ("ftp://ozon.ru/product/1/", "https://"),
    ("https://www.ozon.ru/product/1/ https://www.ozon.ru/product/2/", "по одной ссылке в строке"),
    ("   ", "Пустая строка"),
])
def test_rejected_links(text, error):
    with pytest.raises(tl.LinkError) as exc:
        tl.parse_link(text)
    assert error in str(exc.value)


def test_own_params_kept_old_utm_replaced():
    link = tl.parse_link("https://www.ozon.ru/product/1/?asb=abc&utm_source=old&UTM_campaign=x&avtc=1#reviews")
    labels = tl.Labels(campaign=f"{PREFIX}_vk", source="vk", medium="cpc")
    url = tl.tagged_url(link, labels)
    assert url.startswith("https://www.ozon.ru/product/1/?asb=abc&avtc=1&")
    assert query(url) == {"asb": "abc", "avtc": "1", "utm_source": "vk", "utm_medium": "cpc", "utm_campaign": f"{PREFIX}_vk"}
    assert "#" not in url


def test_labels_encoded_macros_kept():
    link = tl.parse_link("https://www.ozon.ru/product/1/")
    labels = tl.Labels(campaign=f"{PREFIX}_x", source="блог", medium="post & story", content="{region_name}", term="{{age}}")
    url = tl.tagged_url(link, labels)
    assert "utm_content={region_name}" in url and "utm_term={{age}}" in url  # сеть подставит макрос сама
    assert "post%20%26%20story" in url and "%D0%B1%D0%BB%D0%BE%D0%B3" in url
    assert query(url)["utm_medium"] == "post & story"


# ── префикс и метки ──


@pytest.mark.parametrize("text, value", [
    ("vendor_org_123456", PREFIX), ("  VENDOR_ORG_123456_ ", PREFIX),
])
def test_prefix_ok(text, value):
    assert tl.check_prefix(text) == value


@pytest.mark.parametrize("text, error", [
    ("", "без него Ozon не засчитает ни одного перехода"),
    ("vendor_123456", "vendor_org_ и цифры"),
    ("vendor_org_abc", "vendor_org_ и цифры"),
    ("vendor_org_123456_header_inst", "Префикс — только «vendor_org_123456»"),
])
def test_prefix_errors(text, error):
    with pytest.raises(tl.LinkError) as exc:
        tl.check_prefix(text)
    assert error in str(exc.value)


def test_campaign_name():
    assert tl.campaign_value(PREFIX, " Header Inst ") == f"{PREFIX}_header_inst"
    for bad in ("", "кампания", "a" * 65, "x/y"):
        with pytest.raises(tl.LinkError):
            tl.check_campaign(bad)


def test_ad_network_source_is_fixed():
    labels = tl.make_labels(prefix=PREFIX, campaign="autumn", channel="yandex", source="mine")
    assert (labels.source, labels.medium, labels.campaign) == ("yandex", "cpc", f"{PREFIX}_autumn")
    social = tl.make_labels(prefix=PREFIX, campaign="blog", channel="social", source="inst")
    assert (social.source, social.medium) == ("inst", "social")
    with pytest.raises(tl.LinkError, match="Выберите канал"):
        tl.make_labels(prefix=PREFIX, campaign="x", channel="tv")
    with pytest.raises(tl.LinkError, match="префикс"):
        tl.make_labels(prefix=None, campaign="x", channel="other")


def test_source_and_medium_required():
    """Как в конструкторе Ozon (решение владельца 17.11.2026): utm_source и utm_medium обязательны."""
    with pytest.raises(tl.LinkError, match="Укажите utm_source"):
        tl.make_labels(prefix=PREFIX, campaign="blog", channel="other", medium="post")
    with pytest.raises(tl.LinkError, match="Укажите utm_medium"):
        tl.make_labels(prefix=PREFIX, campaign="blog", channel="other", source="site", medium="  ")
    with pytest.raises(tl.LinkError, match="Укажите utm_medium"):
        tl.make_labels(prefix=PREFIX, campaign="blog", channel="vk", medium="")  # у сети source сам, medium — нет
    # у рекламной сети и «Соцсетей» medium по умолчанию свой — можно не передавать
    assert tl.make_labels(prefix=PREFIX, campaign="x", channel="vk").medium == "cpc"


def test_channels_have_suggestions():
    for ch in tl.CHANNELS.values():
        assert ch.medium_options, ch.key
        assert ch.source_options or ch.fixed_source, ch.key
        if ch.fixed_source:
            assert ch.source_options == (ch.source,)


def test_build_rows_errors_per_line_and_limit():
    labels = tl.Labels(campaign=f"{PREFIX}_x", source="tg", medium="post")
    lines = tl.split_lines("https://www.ozon.ru/product/1/\n\nhttps://example.com/\nozon.ru/product/1/?utm_source=a\n")
    assert [n for n, _ in lines] == [1, 3, 4]
    rows = tl.build_rows([(n, s, None) for n, s in lines], labels)
    assert rows[0].url and rows[0].section == "product"
    assert "ozon.ru" in rows[1].error and rows[1].url is None
    assert rows[2].error == "Повтор: эта ссылка уже есть в списке выше"  # метки старые — ссылка та же
    with pytest.raises(tl.LinkError, match="Не больше 100"):
        tl.build_rows([(i, "https://www.ozon.ru/product/1/", None) for i in range(101)], labels)


# ── API ──


async def seed_products(sessions, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        items = {
            "A": Product(user_id=user_id, ozon_product_id=1, ozon_sku=900001, offer_id="6001-2RS", name="Подшипник 6001",
                         barcode="4600000000001", synced_at=now),
            "B": Product(user_id=user_id, ozon_product_id=2, ozon_sku=None, offer_id="NEW-1", name="Без SKU", synced_at=now),
            "E": Product(user_id=user_id, ozon_product_id=3, ozon_sku=900003, offer_id="OLD-1", name="Архивный",
                         is_archived=True, synced_at=now),
        }
        db.add_all(items.values())
        await db.commit()
        return {k: p.id for k, p in items.items()}


async def me(client, tokens) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=auth(tokens))).json()["id"])


async def test_prefix_setting(client, tokens):
    assert (await client.get(f"{API}/settings")).status_code == 401
    r = await client.get(f"{API}/settings", headers=auth(tokens))
    body = r.json()
    assert body["utm_prefix"] is None and body["max_links"] == 100
    assert body["cabinet_url"] == "https://seller.ozon.ru/app/advertisement/vendor-traffic"
    assert [c["key"] for c in body["channels"]] == ["yandex", "mytarget", "vk", "social", "other"]
    assert body["channels"][0]["fixed_source"] and body["channels"][0]["macros"]
    social = body["channels"][3]
    assert "inst" in social["source_options"] and "story" in social["medium_options"]

    r = await client.put(f"{API}/settings", json={"utm_prefix": PREFIX}, headers=auth(tokens))
    r = await client.post(f"{API}/links/build", json={"channel": "social", "campaign": "x", "source": "inst", "medium": "",
                                                      "urls": "ozon.ru/product/1/"}, headers=auth(tokens))
    assert r.status_code == 400 and "Укажите utm_medium" in r.json()["detail"]
    await client.put(f"{API}/settings", json={"utm_prefix": None}, headers=auth(tokens))

    r = await client.put(f"{API}/settings", json={"utm_prefix": "vendor_org_123456_promo"}, headers=auth(tokens))
    assert r.status_code == 400 and "Префикс — только" in r.json()["detail"]
    r = await client.put(f"{API}/settings", json={"utm_prefix": " Vendor_Org_123456 "}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["utm_prefix"] == PREFIX
    assert (await client.get(f"{API}/settings", headers=auth(tokens))).json()["utm_prefix"] == PREFIX
    # у других настроек префикс не трогается, и наоборот
    await client.patch("/api/v1/settings", json={"stock_low_days": 20}, headers=auth(tokens))
    assert (await client.get(f"{API}/settings", headers=auth(tokens))).json()["utm_prefix"] == PREFIX
    r = await client.put(f"{API}/settings", json={"utm_prefix": ""}, headers=auth(tokens))
    assert r.json()["utm_prefix"] is None


async def test_build_without_prefix_asks_for_it(client, tokens):
    r = await client.post(f"{API}/links/build", json={"channel": "other", "campaign": "x", "urls": "ozon.ru/product/1/"},
                          headers=auth(tokens))
    assert r.status_code == 400 and "UTM_CAMPAIGN" in r.json()["detail"]  # префикс — первым, раньше source и medium


async def test_product_search(client, tokens, sessions):
    await seed_products(sessions, await me(client, tokens))
    find = lambda s: client.get(f"{API}/products", params={"search": s}, headers=auth(tokens))  # noqa: E731
    assert [p["offer_id"] for p in (await find("")).json()] == ["6001-2RS", "NEW-1", "OLD-1"]  # архив — в конце
    assert [p["offer_id"] for p in (await find("подшип")).json()] == ["6001-2RS"]
    assert [p["offer_id"] for p in (await find("900003")).json()] == ["OLD-1"]
    assert [p["offer_id"] for p in (await find("4600000000001")).json()] == ["6001-2RS"]


async def test_build_products_and_urls(client, tokens, sessions):
    ids = await seed_products(sessions, await me(client, tokens))
    await client.put(f"{API}/settings", json={"utm_prefix": PREFIX}, headers=auth(tokens))
    body = {
        "product_ids": [str(ids["A"]), str(ids["B"]), str(uuid.uuid4()), str(ids["A"])],
        "urls": "https://www.ozon.ru/brand/nachi-1/\nhttps://www.ozon.ru/category/x/\n\nhttps://www.ozon.ru/product/900001/",
        "channel": "social", "campaign": "Header Inst", "source": "inst", "medium": "description",
        "content": "link_in_bio",
    }
    r = await client.post(f"{API}/links/build", json=body, headers=auth(tokens))
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["campaign"], res["ok"], res["errors"]) == (f"{PREFIX}_header_inst", 2, 3)
    a, b, brand, cat, dup = res["rows"]
    assert a["kind"] == "product" and a["product"]["offer_id"] == "6001-2RS" and a["line"] is None
    assert query(a["url"]) == {"utm_source": "inst", "utm_medium": "description", "utm_campaign": f"{PREFIX}_header_inst",
                               "utm_content": "link_in_bio"}
    assert a["url"].startswith("https://www.ozon.ru/product/900001/?")
    assert "нет SKU" in b["error"] and b["url"] is None
    assert (brand["kind"], brand["line"], brand["section"]) == ("url", 1, "brand")
    assert cat["line"] == 2 and "только ссылки на товар" in cat["error"]
    assert dup["line"] == 4 and dup["error"].startswith("Повтор")  # та же страница, что у товара A
    assert res["warnings"] == []

    r = await client.post(f"{API}/links/build", json={**body, "campaign": ""}, headers=auth(tokens))
    assert r.status_code == 400 and "название кампании" in r.json()["detail"]
    r = await client.post(f"{API}/links/build", json={"channel": "vk", "campaign": "x"}, headers=auth(tokens))
    assert r.status_code == 400 and "Выберите товары или вставьте ссылки" in r.json()["detail"]


async def test_other_store_products_are_not_used(client, tokens, sessions):
    ids = await seed_products(sessions, await me(client, tokens))
    await client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "strongpass1"})
    other = (await client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strongpass1"})).json()
    await client.put(f"{API}/settings", json={"utm_prefix": "vendor_org_999"}, headers=auth(other))
    assert (await client.get(f"{API}/settings", headers=auth(tokens))).json()["utm_prefix"] is None  # префикс — свой
    r = await client.post(f"{API}/links/build", json={"product_ids": [str(ids["A"])], "channel": "vk", "campaign": "x"},
                          headers=auth(other))
    assert r.status_code == 400  # чужой товар — как будто не выбран
    assert (await client.get(f"{API}/products", headers=auth(other))).json() == []


async def test_export_xlsx_csv_and_qr(client, tokens, sessions):
    ids = await seed_products(sessions, await me(client, tokens))
    await client.put(f"{API}/settings", json={"utm_prefix": PREFIX}, headers=auth(tokens))
    body = {"product_ids": [str(ids["A"]), str(ids["B"])], "urls": "https://www.ozon.ru/highlight/sale-1/",
            "channel": "vk", "campaign": "october"}

    r = await client.post(f"{API}/links/export", json=body, headers=auth(tokens))
    assert r.status_code == 200 and "utm-links.xlsx" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][:5] == ("Что размечено", "Артикул", "Название или исходная ссылка", "Раздел Ozon", "Ссылка с метками")
    assert [r[0] for r in rows[1:]] == ["Товар", "Ссылка"]  # товар без SKU — не выгружается
    assert rows[1][1] == "6001-2RS" and rows[2][3] == "Акция или подборка" and rows[2][7] == f"{PREFIX}_october"

    r = await client.post(f"{API}/links/export", params={"format": "csv"}, json=body, headers=auth(tokens))
    text = r.content.decode("utf-8-sig")
    table = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert len(table) == 3 and query(table[1][4])["utm_source"] == "vk"

    url = tl.tagged_url(tl.parse_link("https://www.ozon.ru/product/900001/"), tl.Labels(campaign=f"{PREFIX}_x"))
    r = await client.get(f"{API}/qr", params={"url": url}, headers=auth(tokens))
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"<svg" in r.content
    r = await client.get(f"{API}/qr", params={"url": url, "format": "png", "download": "true"}, headers=auth(tokens))
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and "qr.png" in r.headers["content-disposition"]
    r = await client.get(f"{API}/qr", params={"url": "https://example.com/"}, headers=auth(tokens))
    assert r.status_code == 400


async def test_build_is_not_a_change(client, tokens, sessions):
    """Сборка и выгрузка ничего не меняют — в журнал действий не пишутся; смена префикса — событие ленты."""
    from sqlalchemy import select

    from app.models.audit import AuditLog

    await seed_products(sessions, await me(client, tokens))
    await client.put(f"{API}/settings", json={"utm_prefix": PREFIX}, headers=auth(tokens))
    r = await client.post(f"{API}/links/build", json={"channel": "other", "campaign": "x", "source": "site",
                                                      "medium": "banner", "urls": "ozon.ru/product/1/"}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["ok"] == 1
    async with sessions() as db:
        paths = [a.entity_id for a in (await db.scalars(select(AuditLog))).all()]
    assert "/api/v1/traffic/settings" in paths and "/api/v1/traffic/links/build" not in paths
