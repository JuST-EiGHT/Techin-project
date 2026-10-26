"""Уведомления по разделам (шаг 10.25): разделы карты, перенос прежних категорий Telegram и порогов «Настроек» без потери
значений, каналы, выключенный раздел не тревожит на сайте, важность, сводка раз в день, тексты с разделом и магазином."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.engine import anomaly_detector as det
from app.engine import notifications as engine
from app.engine.anomaly_text import Text
from app.models.anomaly import Anomaly
from app.models.notification import NotificationLog, NotificationSettings
from app.services import notifier
from tests.test_notifications import CHAT, SITE, link, tg  # noqa: F401 — фикстура фейкового Telegram
from tests.test_products import user_id_of

URL = "/api/v1/notifications"
MSK = timezone(timedelta(hours=3))


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ---------- engine ----------


def test_sections_cover_every_anomaly_once():
    assert [x.key for x in engine.SECTIONS] == [
        "sales", "margin", "stock", "cash", "payouts", "auto_promo", "prices", "data", "automation"]
    assert sorted(engine.SECTION_OF) == sorted(det.TYPES)  # каждая аномалия — ровно в одном разделе
    assert engine.BY_KEY["automation"].soon == "9.x" and engine.BY_KEY["automation"].types == ()
    assert engine.section_of(det.PROMO_LOSS) == "margin" and engine.section_of(det.UNCATEGORIZED) == "cash"
    assert engine.section_of(det.COMMISSION_CHANGE) == "prices" and engine.section_of(det.EXCESS_STOCK) == "stock"


def test_legacy_categories_become_sections():
    """Категории до 10.25 → разделы: выбор пользователя не теряется."""
    s = engine.legacy_sections(red=False, yellow=True, auto_promo=False, money=False, data=True)
    got = {k: (v.telegram, v.severity) for k, v in s.items()}
    assert got == {
        "sales": (True, "all"), "margin": (True, "all"), "stock": (True, "all"), "prices": (True, "all"),
        "cash": (False, "all"), "payouts": (False, "all"), "auto_promo": (False, "all"), "data": (True, "all"),
        "automation": (True, "all"),
    }
    default = engine.Prefs()
    assert engine.wanted(default, det.LOW_STOCK, "red") and not engine.wanted(default, det.LOW_STOCK, "yellow")
    assert engine.wanted(default, det.PAYOUT_DIFF, "yellow") and engine.wanted(default, det.PROMO_AUTO_ADDED, "yellow")
    assert not engine.wanted(default, det.SYNC_PROBLEM, "red")  # данные — по умолчанию не шлём
    # сохранённые разделы: что есть — из JSON, остального нет — как перенесено
    parsed = engine.parse_sections({"stock": {"on": False}, "sales": {"severity": "bogus", "daily": True}}, s)
    assert parsed["stock"] == engine.SectionPrefs(on=False, telegram=True, severity="all", daily=False)
    assert parsed["sales"] == engine.SectionPrefs(on=True, telegram=True, severity="all", daily=True)
    assert parsed["cash"] == s["cash"]


def test_severity_mute_and_daily():
    p = engine.Prefs({**engine.DEFAULT_SECTIONS, "prices": engine.SectionPrefs(severity="red"),
                      "auto_promo": engine.SectionPrefs(severity="red"),
                      "stock": engine.SectionPrefs(on=False), "payouts": engine.SectionPrefs(daily=True)})
    assert engine.wanted(p, det.PRICE_CHANGE, "red") and not engine.wanted(p, det.COMMISSION_CHANGE, "yellow")
    assert engine.wanted(p, det.PROMO_AUTO_ADDED, "yellow")  # у раздела одна важность — выбор не действует
    assert not engine.wanted(p, det.LOW_STOCK, "red") and not engine.shown(p, det.EXCESS_STOCK)  # выключен — нигде
    assert p.muted_types() == {det.LOW_STOCK, det.EXCESS_STOCK}
    assert "остатки" not in " ".join(p.enabled()) and "выплаты Ozon и банк (сводкой)" in p.enabled()

    msk = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=MSK)  # noqa: E731
    # сводка — в 09:00 по поясу пользователя: сегодня, если ещё не было, иначе завтра; в тихие часы — после них
    assert engine.next_digest(p, msk(26, 7)) == datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
    assert engine.next_digest(p, msk(26, 9)) == datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)
    quiet = engine.Prefs(p.sections, quiet_enabled=True, quiet_from=22 * 60, quiet_to=10 * 60, digest_at=9 * 60)
    assert engine.next_digest(quiet, msk(26, 12)) == datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc)  # 10:00 МСК
    vl = engine.Prefs(p.sections, timezone="Asia/Vladivostok", digest_at=8 * 60 + 30)
    assert engine.next_digest(vl, msk(26, 12)) == datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)  # 08:30 VL
    now = msk(26, 12)
    assert engine.send_after(p, det.BANK_DIFF, now) == engine.next_digest(p, now)
    assert engine.send_after(p, det.SALES_DROP, now) == now


def test_texts_with_section_and_store():
    one = engine.Item("red", Text("Выплата не дошла", "Проверьте", "/dashboard/finance/payouts#payout-2026-10-21"),
                      store="Склад Юг", store_id="s1", section="payouts")
    text = engine.render_one(one, SITE)
    assert "Раздел: Выплаты Ozon и банк · Магазин: Склад Юг" in text
    assert f'href="{SITE}/dashboard/finance/payouts?store=s1#payout-2026-10-21"' in text  # магазин — до якоря
    items = [engine.Item("yellow", Text("Цена выросла", "", "/p"), section="prices"),
             engine.Item("red", Text("Продажи упали", "", "/s"), section="sales"),
             engine.Item("yellow", Text("Продажи выросли", "", "/s2"), section="sales")]
    digest = engine.render_digest(items, SITE, daily_digest=True)
    assert digest.startswith("🔔 <b>Сводка за день: 3</b>")
    assert digest.index("<b>Продажи</b>") < digest.index("Продажи упали") < digest.index("Продажи выросли") \
        < digest.index("<b>Изменения цен</b>")  # по разделам в порядке карты, внутри — красные первыми


# ---------- API: разделы и каналы ----------


async def test_settings_sections_channels_and_legacy(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    async with sessions() as db:  # настройки до 10.25: жёлтые да, деньги нет, данные да
        db.add(NotificationSettings(user_id=uid, notify_red=True, notify_yellow=True, notify_money=False, notify_data=True))
        await db.commit()
    body = (await client.get(f"{URL}/settings", headers=auth(tokens))).json()
    sections = {x["key"]: x for x in body["sections"]}
    assert list(sections) == ["sales", "margin", "stock", "cash", "payouts", "auto_promo", "prices", "data", "automation"]
    assert (sections["sales"]["telegram"], sections["sales"]["severity"]) == (True, "all")  # перенесено без потери
    assert sections["payouts"]["telegram"] is False and sections["data"]["telegram"] is True
    assert sections["margin"]["severities"] == ["red"] and sections["automation"]["soon"] == "9.x"
    assert all(x["on"] and not x["daily"] for x in body["sections"])
    channels = {c["key"]: c for c in body["channels"]}
    assert channels["service"]["connected"] is True and channels["email"]["available"] is False
    assert channels["email"]["note"].startswith("Скоро") and body["digest_at"] == "09:00"

    patch = lambda b: client.patch(f"{URL}/settings", json=b, headers=auth(tokens))  # noqa: E731
    r = await patch({"sections": {"stock": {"on": False}, "payouts": {"telegram": True, "daily": True}},
                     "digest_at": "08:30"})
    assert r.status_code == 200
    sections = {x["key"]: x for x in r.json()["sections"]}
    assert sections["stock"]["on"] is False and (sections["payouts"]["telegram"], sections["payouts"]["daily"]) == (True, True)
    assert sections["sales"]["severity"] == "all" and r.json()["digest_at"] == "08:30"  # остальное — как было
    async with sessions() as db:
        row = await db.get(NotificationSettings, uid)
        assert row.sections["stock"]["on"] is False and row.digest_at == 8 * 60 + 30
    assert (await patch({"sections": {"automation": {"on": False}}})).json()["detail"].startswith("Раздел «Автоматизация»")
    assert (await patch({"sections": {"bogus": {"on": False}}})).status_code == 400
    assert (await patch({"digest_at": "9:00"})).status_code == 400  # формат ЧЧ:ММ


async def test_muted_section_not_in_bell_but_on_page(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        for type_, sev, key in ((det.LOW_STOCK, "red", "product:a"), (det.EXCESS_STOCK, "yellow", "product:b"),
                                (det.SALES_DROP, "yellow", "shop")):
            db.add(Anomaly(id=uuid.uuid4(), user_id=uid, type=type_, key=key, severity=sev, detected_at=now,
                           last_seen_at=now))
        await db.commit()
    count = lambda: client.get("/api/v1/anomalies/count", headers=auth(tokens))  # noqa: E731
    assert (await count()).json()["unread"] == 3
    page = (await client.get("/api/v1/anomalies", headers=auth(tokens))).json()
    assert page["by_section"] == {"stock": 2, "sales": 1} and {i["section"] for i in page["items"]} == {"stock", "sales"}

    await client.patch(f"{URL}/settings", json={"sections": {"stock": {"on": False}}}, headers=auth(tokens))
    assert (await count()).json() | {} == {"active": 1, "unread": 1, "red": 0, "yellow": 1, "red_active": 0,
                                           "yellow_active": 1}
    page = (await client.get("/api/v1/anomalies", headers=auth(tokens))).json()
    assert [i["type"] for i in page["items"]] == [det.SALES_DROP]  # колокольчик и «Требует внимания» — без выключенных
    full = (await client.get("/api/v1/anomalies", params={"include_off": "true"}, headers=auth(tokens))).json()
    assert full["total"] == 3 and {i["type"]: i["muted"] for i in full["items"]} == {
        det.LOW_STOCK: True, det.EXCESS_STOCK: True, det.SALES_DROP: False}
    assert full["by_section"] == {"stock": 2, "sales": 1}
    only = (await client.get("/api/v1/anomalies", params={"section": "stock", "include_off": "true"},
                             headers=auth(tokens))).json()
    assert only["total"] == 2
    # «Отметить все прочитанными» — только видимые: выключенные не трогаем
    r = await client.post("/api/v1/anomalies/acknowledge-all", json={}, headers=auth(tokens))
    assert r.json()["acknowledged"] == 1


async def test_daily_digest_and_off_section_in_telegram(client, tokens, sessions, tg):
    uid = await user_id_of(client, tokens)
    await link(sessions, uid)
    await client.patch(f"{URL}/settings", json={"sections": {"stock": {"daily": True}, "sales": {"on": False}}},
                       headers=auth(tokens))
    now = datetime.now(timezone.utc)
    ids = []
    async with sessions() as db:
        for type_, sev, key in ((det.LOW_STOCK, "red", "product:a"), (det.SALES_DROP, "red", "shop"),
                                (det.PAYOUT_MISSED, "red", "payout:2026-10-21")):
            a = Anomaly(id=uuid.uuid4(), user_id=uid, type=type_, key=key, severity=sev, detected_at=now,
                        last_seen_at=now, details={"day": "2026-10-21"} if type_ == det.PAYOUT_MISSED else None)
            db.add(a)
            ids.append(a.id)
        await db.commit()
        assert await notifier.queue(db, uid, ids, now=now) == 2  # продажи выключены — в очередь не попали
        rows = {r.anomaly_id: r for r in (await db.execute(select(NotificationLog))).scalars()}
        prefs = notifier.prefs(await db.get(NotificationSettings, uid))
        digest_at = engine.next_digest(prefs, now)
        assert rows[ids[0]].send_after.replace(tzinfo=timezone.utc) == digest_at  # остатки — сводкой
        assert rows[ids[2]].send_after.replace(tzinfo=timezone.utc) == now  # выплаты — сразу
        stats = await notifier.send_due(db, now=now)
        assert stats["sent"] == 1 and "Раздел: Выплаты Ozon и банк" in tg.sent[0]["text"]
        assert "/dashboard/finance/payouts" in tg.sent[0]["text"]
        stats = await notifier.send_due(db, now=digest_at + timedelta(minutes=1))
    assert stats["sent"] == 1 and tg.sent[1]["text"].startswith("🔔 <b>Сводка за день: 1</b>")
    assert "<b>Остатки</b>" in tg.sent[1]["text"]


# ---------- пороги: переехали из «Настроек» ----------


async def test_thresholds_moved_without_losing_values(client, tokens):
    # значения, заданные раньше в «Настройках» (7.1) …
    r = await client.patch("/api/v1/settings", json={"anomaly_sales_drop_pct": 35, "low_margin_pct": "12.5",
                                                     "anomaly_sync_stale_hours": 48}, headers=auth(tokens))
    assert r.status_code == 200
    body = (await client.get(f"{URL}/thresholds", headers=auth(tokens))).json()
    values = {i["key"]: (i["value"], i["default"]) for s in body["sections"] for i in s["items"]}
    assert values["anomaly_sales_drop_pct"] == ("35", "40") and values["low_margin_pct"] == ("12.5", "10")
    assert values["anomaly_sync_stale_hours"] == ("48", "24") and values["anomaly_price_red_pct"] == ("30", "30")
    assert body["defaults"] is False and body["stock"] == {"critical": 7, "low": 14, "excess": 60}
    by_section = {s["section"]: [i["key"] for i in s["items"]] for s in body["sections"]}
    assert by_section["prices"] == ["anomaly_price_pct", "anomaly_price_red_pct", "anomaly_commission_pp"]
    assert by_section["stock"] == [] and by_section["auto_promo"] == []
    assert len(values) == 12  # все пороги аномалий — на месте

    # … меняются здесь — и те же значения видят «Настройки» и проверки
    patch = lambda b: client.patch(f"{URL}/thresholds", json=b, headers=auth(tokens))  # noqa: E731
    r = await patch({"values": {"anomaly_price_pct": 15, "anomaly_commission_pp": "2.5"}})
    assert r.status_code == 200
    s = (await client.get("/api/v1/settings", headers=auth(tokens))).json()
    assert (s["anomaly_price_pct"], s["anomaly_commission_pp"], s["anomaly_sales_drop_pct"]) == (15, "2.5", 35)
    assert (await patch({"values": {"anomaly_price_pct": 40}})).json()["detail"] == "Изменение цены: жёлтый порог меньше красного"
    assert (await patch({"values": {"anomaly_commission_pp": "2.55"}})).json()["detail"].startswith("«Комиссия изменилась")
    assert (await patch({"values": {"anomaly_cash_gap_days": 0}})).status_code == 400
    assert (await patch({"values": {"stock_low_days": 3}})).json()["detail"] == "Порог: выберите из списка"
    r = await patch({"reset": True})
    assert r.json()["defaults"] is True
    s = (await client.get("/api/v1/settings", headers=auth(tokens))).json()
    assert (s["anomaly_price_pct"], s["low_margin_pct"], s["anomaly_defaults"]) == (10, "10", True)
