"""Шаг 10.11: «История изменений и событий» — каждый источник попадает в ленту (цены через сервис и в кабинете, списком —
одним событием, акции, остатки, аномалии, действия, отчёты, ошибки загрузки), повторы и служебное — нет; фильтры по виду,
товару и периоду; ссылки; страницы по курсору без пропусков и повторов; каждый изменяющий эндпоинт описан."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.engine import event_feed as ef
from app.main import app
from app.models.anomaly import ESCALATED, NORMAL, RED, YELLOW, Anomaly
from app.models.audit import HTTP_ENTITY, OK, REJECTED, AuditLog
from app.models.price_change import APPLIED, FAILED, MANUAL_BULK, MANUAL_SINGLE, PREVIEW, REJECTED as PC_REJECTED
from app.models.price_change import PriceChange
from app.models.product import Product, ProductPrice
from app.models.promotion import Promotion, PromotionProduct
from app.models.report import ERROR, READY, Report
from app.models.sync_run import FAILED as SYNC_FAILED
from app.models.sync_run import SUCCESS, SyncRun
from app.models.warehouse import ProductStock, Warehouse
from tests.test_products import user_id_of

D = Decimal
BASE = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=5)


def t(hours: float) -> datetime:
    return BASE + timedelta(hours=hours)


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_every_mutating_endpoint_described():
    """Новый изменяющий эндпоинт — опишите его в ACTIONS (событие ленты) или IGNORED (не событие)."""
    known = set(ef.ACTIONS) | ef.IGNORED | {ef.REPORT_UPLOAD}
    missing = [(m, r.path) for r in app.routes for m in sorted(getattr(r, "methods", None) or set())
               if m in ("POST", "PUT", "PATCH", "DELETE") and (m, r.path) not in known]
    assert missing == []


def test_action_texts_and_cursor():
    e = ef.action("a", t(0), "PUT", ef.COSTS_TEMPLATE, "/api/v1/costs/6201-2RS")
    assert (e.title, e.link) == ("Изменена себестоимость 6201-2RS", "/dashboard/products/costs?search=6201-2RS")
    rid = uuid.uuid4()
    e = ef.action("a", t(0), "PATCH", "/api/v1/reports/{report_id}", f"/api/v1/reports/{rid}")
    assert e.link == f"/dashboard/analytics/reports/{rid}" and ef.action("a", t(0), "POST", "/api/v1/sync/all", None) is None
    c = ef.encode_cursor(t(1), "price:x")
    assert ef.decode_cursor(c) == (t(1), "price:x") and ef.decode_cursor("испорчен") is None


async def seed(sessions, uid) -> dict:
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        x = Product(user_id=uid, ozon_product_id=1, ozon_sku=11, offer_id="X-1", name="Подшипник X", synced_at=now)
        y = Product(user_id=uid, ozon_product_id=2, ozon_sku=22, offer_id="Y-2", name="Подшипник Y", synced_at=now)
        w1 = Warehouse(user_id=uid, ozon_warehouse_id=1, name="Хоругвино", type="FBO", cluster_name="Москва", synced_at=now)
        w2 = Warehouse(user_id=uid, ozon_warehouse_id=2, name="Казань", type="FBO", cluster_name="Казань", synced_at=now)
        db.add_all([x, y, w1, w2])
        await db.flush()
        pc = lambda at, **kw: PriceChange(user_id=uid, offer_id=kw.pop("offer", "X-1"), created_at=at,  # noqa: E731
                                          product_id=kw.pop("product", x.id), change_type=kw.pop("type", MANUAL_SINGLE),
                                          **kw)
        applied = pc(t(1), price_before=D(100), price_after=D(120), status=APPLIED, is_applied=True)
        db.add(applied)
        await db.flush()
        batch = uuid.uuid4()
        db.add_all([
            pc(t(0.5), price_before=D(100), price_after=D(110), status=PREVIEW, is_dry_run=True),  # предпросмотр — нет
            pc(t(2), price_before=D(120), price_after=D(100), status=APPLIED, is_applied=True, rollback_of_id=applied.id),
            pc(t(3), price_before=D(100), price_after=D(300), status=FAILED, error="Ozon не ответил"),
            pc(t(4), type=MANUAL_BULK, batch_id=batch, price_before=D(100), price_after=D(105), status=APPLIED,
               is_applied=True),
            pc(t(4.01), type=MANUAL_BULK, batch_id=batch, product=y.id, offer="Y-2", price_before=D(200),
               price_after=D(210), status=PC_REJECTED),
            pc(t(3.9), type=MANUAL_BULK, batch_id=batch, price_before=D(100), price_after=D(105), status=PREVIEW),
        ])
        # снимки цены: X — изменение через сервис (не событие), Y — в кабинете Ozon
        db.add_all([ProductPrice(product_id=x.id, snapshot_at=t(0), price=D(100)),
                    ProductPrice(product_id=x.id, snapshot_at=t(1.1), price=D(120)),
                    ProductPrice(product_id=y.id, snapshot_at=t(0), price=D(200)),
                    ProductPrice(product_id=y.id, snapshot_at=t(5), price=D(220), commission_pct=D(20)),
                    ProductPrice(product_id=y.id, snapshot_at=t(5.5), price=D(220), commission_pct=D(25))])  # цена та же
        promo = Promotion(user_id=uid, ozon_action_id=5, title="Распродажа", synced_at=now)
        db.add(promo)
        await db.flush()
        db.add(PromotionProduct(promotion_id=promo.id, product_id=x.id, action_price=D(90), is_active=True,
                                added_automatically=True, first_seen_at=t(6), left_at=t(7), synced_at=now))
        db.add(PromotionProduct(promotion_id=promo.id, product_id=y.id, is_active=False, first_seen_at=t(6), synced_at=now))
        # остатки: Y — 5 (первый — только запомнить) → 0 (закончился) → 3 (поставка); X — склады поменялись местами
        db.add_all([ProductStock(product_id=y.id, warehouse_id=w1.id, snapshot_at=t(0), present=5),
                    ProductStock(product_id=y.id, warehouse_id=w1.id, snapshot_at=t(8), present=0),
                    ProductStock(product_id=y.id, warehouse_id=w1.id, snapshot_at=t(9), present=3),
                    ProductStock(product_id=x.id, warehouse_id=w1.id, snapshot_at=t(0), present=4),
                    ProductStock(product_id=x.id, warehouse_id=w2.id, snapshot_at=t(0), present=0),
                    ProductStock(product_id=x.id, warehouse_id=w1.id, snapshot_at=t(8.5), present=0),
                    ProductStock(product_id=x.id, warehouse_id=w2.id, snapshot_at=t(8.5), present=4)])
        an = lambda **kw: Anomaly(user_id=uid, metric_before=D(10), metric_after=D(4), threshold=D(40),  # noqa: E731
                                  details={"change_pct": -60, "days": 3, "actual": 4, "expected": 10}, **kw)
        db.add_all([
            an(type="sales_drop", key=f"product:{y.id}", severity=RED, product_id=y.id, detected_at=t(10),
               last_seen_at=t(11), resolved_at=t(11), resolution=NORMAL),
            an(type="sales_drop", key="shop", severity=YELLOW, detected_at=t(10.5), last_seen_at=t(10.6),
               resolved_at=t(10.6), resolution=ESCALATED),
        ])
        log = lambda at, method, template, path, status=OK: AuditLog(  # noqa: E731
            user_id=uid, created_at=at, action=f"http.{method.lower()}", entity_type=HTTP_ENTITY, entity_id=template,
            status=status, method=method, path=path, http_status=200 if status == OK else 400)
        db.add_all([
            log(t(12), "PUT", ef.COSTS_TEMPLATE, "/api/v1/costs/X-1"),
            log(t(12.1), "PATCH", "/api/v1/settings", "/api/v1/settings"),
            log(t(12.2), "POST", "/api/v1/sync/all", "/api/v1/sync/all"),  # служебное — нет
            log(t(12.3), "PUT", ef.COSTS_TEMPLATE, "/api/v1/costs/X-1", REJECTED),  # не прошло — нет
            log(t(12.4), "POST", "/api/v1/reports/upload", "/api/v1/reports/upload", REJECTED),
        ])
        ok = Report(user_id=uid, filename="week.xlsx", report_type="accrual", period="07.09–13.09.2026", status=READY,
                    summary_json={}, file_sha256="a" * 64, file_size=1, uploaded_at=t(13), parsed_at=t(13))
        bad = Report(user_id=uid, filename="month.xlsx", report_type="realization", period="", status=ERROR,
                     error_message="Не нашли таблицу", summary_json={}, file_sha256="b" * 64, file_size=1,
                     uploaded_at=t(1), parsed_at=t(13.5))
        db.add_all([ok, bad])
        for h, status in ((14, SUCCESS), (15, SYNC_FAILED), (16, SYNC_FAILED), (17, SUCCESS), (18, SYNC_FAILED)):
            db.add(SyncRun(user_id=uid, kind="stocks", status=status, started_at=t(h), finished_at=t(h),
                           error="Ozon не отвечает" if status == SYNC_FAILED else None,
                           stats={"error_kind": "ozon_down"} if status == SYNC_FAILED else None))
        await db.commit()
        return {"x": x.id, "y": y.id, "promo": promo.id, "report": ok.id, "bad": bad.id, "batch": batch}


async def feed(client, tokens, **params):
    r = await client.get("/api/v1/analytics/events", params={"limit": 200, **params}, headers=auth(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def test_every_source_in_feed(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    ids = await seed(sessions, uid)
    body = await feed(client, tokens)
    items = body["items"]
    assert body["next"] is None
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)  # новые — сверху
    titles = [i["title"] for i in items]
    by = {i["title"]: i for i in items}
    # цены: через сервис — вручную, откат, не изменена; списком — одно событие; в кабинете — только Y
    assert by["Цена X-1: 100 ₽ → 120 ₽"]["detail"] == "через сервис, вручную"
    assert by["Цена X-1: 120 ₽ → 100 ₽"]["detail"] == "через сервис, откат"
    failed = by["Цена X-1: 100 ₽ → 300 ₽ — не изменена"]
    assert failed["tone"] == "bad" and "ошибка запроса к Ozon" in failed["detail"]
    bulk = by["Цены списком: 2 товара"]
    assert (bulk["detail"], bulk["tone"]) == ("через сервис, списком: применено у 1, не изменено у 1", "warn")
    cabinet = by["Цена Y-2: 200 ₽ → 220 ₽ ↑"]
    assert cabinet["detail"].startswith("не через сервис") and cabinet["link"] == f"/dashboard/products/prices/history?product={ids['y']}"
    assert sum(t.startswith("Цена X-1: 100 ₽ → 120 ₽") for t in titles) == 1  # снимок изменения сервиса — не отдельно
    assert not any("110" in t for t in titles)  # предпросмотры — не события
    # акции: только участие, вход с «Ozon добавил сам» и выход
    promo_in = by["X-1 вошёл в акцию «Распродажа»"]
    assert promo_in["detail"] == "Ozon добавил сам, цена в акции 90 ₽" and promo_in["link"] == f"/dashboard/products/promotions/{ids['promo']}"
    assert "X-1 вышел из акции «Распродажа»" in by and "Y-2 вошёл в акцию «Распродажа»" not in by
    # остатки: Y закончился и пришёл; у X склады поменялись местами — событий нет
    assert by["Y-2 закончился на складах"]["kind"] == "stock"
    assert by["Y-2 снова в наличии"]["detail"] == "пришла поставка: на складах 3 шт."
    assert not any(t.startswith("X-1 закончился") or t.startswith("X-1 снова") for t in titles)
    # аномалии: появилась (красная) и прошла; «стала красной» — не событие
    found = [i for i in items if i["kind"] == "anomaly" and i["title"].startswith("Продажи")]
    assert len(found) == 2 and {i["tone"] for i in found} == {"bad", "warn"}
    assert by["Прошло: падение продаж — Y-2"]["detail"] == "вернулось в норму"
    assert sum(t.startswith("Прошло:") for t in titles) == 1
    # действия: понятным текстом, служебное и не прошедшее — нет; загрузка отчёта — по отчёту
    assert by["Изменена себестоимость X-1"]["link"] == "/dashboard/products/costs?search=X-1"
    assert "Изменены настройки" in by and sum(t.startswith("Изменена себестоимость") for t in titles) == 1
    assert by["Загружен отчёт за 07.09–13.09.2026"]["link"] == f"/dashboard/analytics/reports/{ids['report']}"
    # ошибки загрузки: начало сбоя — дважды (повтор — нет), отчёт с ошибкой, файл не загрузился
    sync = [i for i in items if i["title"] == "Не загружаются данные: остатки"]
    assert len(sync) == 2 and sync[0]["detail"].startswith("Ozon временно не отвечает") and sync[0]["link"] == "/dashboard?sync=1"
    assert by["Отчёт «month.xlsx» не удалось перечитать"]["detail"] == "Не нашли таблицу"
    assert by["Отчёт не загрузился"]["kind"] == "error"
    assert {i["kind"] for i in items} == set(ef.KINDS)


async def test_filters_and_pages(client, tokens, sessions):
    uid = await user_id_of(client, tokens)
    ids = await seed(sessions, uid)
    everything = (await feed(client, tokens))["items"]

    prices = (await feed(client, tokens, kind="price"))["items"]
    assert prices and {i["kind"] for i in prices} == {"price"}
    two = (await feed(client, tokens, kind=["stock", "error"]))["items"]
    assert {i["kind"] for i in two} == {"stock", "error"}

    # товар: его цены (списком — по товару), акции, остатки, аномалии, правка себестоимости; без общих событий
    x = (await feed(client, tokens, product_id=str(ids["x"])))["items"]
    assert {i["product"]["offer_id"] for i in x if i["product"]} == {"X-1"}
    assert "Изменена себестоимость X-1" in [i["title"] for i in x]
    assert "Цена X-1: 100 ₽ → 105 ₽" in [i["title"] for i in x] and not any(i["kind"] == "error" for i in x)
    y = (await feed(client, tokens, product_id=str(ids["y"])))["items"]
    assert {i["kind"] for i in y} == {"price", "stock", "anomaly"}
    assert "Цена Y-2: 200 ₽ → 210 ₽ — не изменена" in [i["title"] for i in y]

    # период: события только этих суток (по Москве)
    day = (BASE + timedelta(hours=3)).date()
    in_day = (await feed(client, tokens, date_from=str(day), date_to=str(day)))["items"]
    assert in_day and all(datetime.fromisoformat(i["at"]).astimezone(timezone(timedelta(hours=3))).date() == day
                          for i in in_day)

    # страницы по 3: все события по порядку, без пропусков и повторов
    keys, before = [], None
    for _ in range(50):
        params = {"limit": 3, **({"before": before} if before else {})}
        r = await client.get("/api/v1/analytics/events", params=params, headers=auth(tokens))
        page = r.json()
        keys += [i["key"] for i in page["items"]]
        if not (before := page["next"]):
            break
    assert keys == [i["key"] for i in everything]

    bad = await client.get("/api/v1/analytics/events", params={"before": "!!!"}, headers=auth(tokens))
    assert bad.status_code == 400 and "обновите страницу" in bad.json()["detail"]
    other = await client.get("/api/v1/analytics/events", params={"product_id": str(uuid.uuid4())}, headers=auth(tokens))
    assert other.status_code == 404


async def test_empty_feed(client, tokens):
    body = await feed(client, tokens)
    assert body["items"] == [] and body["next"] is None and [k["kind"] for k in body["kinds"]] == list(ef.KINDS)
