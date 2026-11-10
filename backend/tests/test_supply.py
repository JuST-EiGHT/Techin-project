"""Планирование поставок (шаг 5.2, BUSINESS_LOGIC §5): расчёт, API, Excel, параметры пользователя."""
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.engine.stocks import Thresholds
from app.engine.supply_planner import (
    CRITICAL,
    OZON,
    PLANNED,
    PURCHASE,
    SALES,
    SUPPLY,
    URGENT,
    ClusterPoint,
    Demand,
    Params,
    Point,
    cluster_recommend,
    plan,
    recommend,
)
from app.models.cluster_metrics import ClusterStockMetric, OzonSupplyRec, OzonSupplyRecSync
from app.models.posting import Posting, PostingItem, PostingSync
from app.models.product import Product
from app.services.sync.finance import today_msk
from tests.test_sync_stocks import FBS_WH, api_user, auth
from tests.test_sync_stocks import ozon as stocks_ozon  # noqa: F401 — фикстура остатков


def point(stock: int, sold_30: int, sold_7: int, transit: int = 0, days_30: int = 30, days_7: int = 7,
          offer: str = "A", dest: str = "москва", kind: str = SUPPLY) -> Point:
    return Point(offer, offer, kind, dest, stock, transit, Demand(sold_30, sold_7, days_30, days_7))


# ── расчёт ──
def test_formula_by_hand():
    """60 шт. за 30 дней (2 в день), за 7 дней — 21 (3 в день, тренд 1,5); на складе 30, в пути 10.
    Хватит на 30 / 3 = 10 дней — «Скоро» (меньше 14); цель 2 × 45 = 90; к приходу поставки (через 7 дней) нужно
    2 × (45 + 7) = 104 → отправить 104 − 30 − 10 = 64."""
    r = recommend(point(30, 60, 21, transit=10))
    assert (r.avg_daily, r.rate, r.trend) == (Decimal("2.000"), Decimal("3.000"), Decimal("1.50"))
    assert r.days_left == Decimal("10.0")
    assert (r.target_stock, r.need, r.to_order, r.urgency, r.arrives_late) == (90, 104, 64, URGENT, False)


def test_out_of_stock_is_critical_and_late():
    r = recommend(point(0, 15, 7))  # 0,5 в день, кончился
    assert (r.days_left, r.urgency, r.arrives_late, r.to_order) == (Decimal("0.0"), CRITICAL, True, 26)  # ⌈0,5 × 52⌉
    r = recommend(point(5, 30, 7))  # 1 в день: хватит на 5 дней — поставка (7 дней) не успеет
    assert (r.urgency, r.arrives_late, r.to_order) == (CRITICAL, True, 47)


def test_no_recommendation():
    assert recommend(point(200, 60, 14)) is None  # запаса хватает: 104 ≤ 200
    assert recommend(point(50, 60, 14, transit=60)) is None  # уже везут
    assert recommend(point(0, 0, 0)) is None  # продаж за 30 дней нет — не планируем


def test_no_sales_last_week_is_planned():
    r = recommend(point(10, 30, 0))
    assert (r.days_left, r.trend, r.urgency, r.to_order) == (None, Decimal("0.00"), PLANNED, 42)


def test_parameters_and_thresholds():
    r = recommend(point(30, 60, 21), Params(target_days=30, lead_time_days=0), Thresholds(3, 5, 60))
    assert (r.need, r.to_order, r.urgency) == (60, 30, PLANNED)  # 10 дней при порогах 3 / 5 — плановая
    r = recommend(point(30, 60, 21), Params(target_days=60, lead_time_days=14))
    assert (r.need, r.to_order, r.arrives_late) == (148, 118, True)  # 10 дней < 14 в пути


def test_partial_coverage():
    """Заказы загружены только за 10 дней — среднее по ним, а не по 30."""
    r = recommend(point(0, 20, 14, days_30=10, days_7=7))
    assert (r.avg_daily, r.need) == (Decimal("2.000"), 104)


def test_plan_order():
    recs = plan([
        point(100, 60, 14, offer="planned"),  # 50 дней
        point(0, 30, 7, offer="empty"),  # 0 дней
        point(20, 60, 14, offer="soon"),  # 10 дней
        point(5, 30, 7, offer="critical", kind=PURCHASE, dest="wh"),  # 5 дней
        point(500, 60, 14, offer="enough"),
    ])
    assert [(r.point.offer_id, r.urgency) for r in recs] == [
        ("empty", CRITICAL), ("critical", CRITICAL), ("soon", URGENT), ("planned", PLANNED),
    ]


# ── 10.18a: склады Ozon по данным Ozon о кластере ──
def cpoint(stock: int, ads: str | None, rec: int | None, transit: int = 0, attention: str | None = None,
           offer: str = "A", dest: str = "москва") -> ClusterPoint:
    return ClusterPoint(offer, offer, dest, stock, transit, None if ads is None else Decimal(ads), rec, attention)


def test_cluster_by_sales_when_more_than_ozon():
    """2 в день по Ozon, на складах кластера 30, в пути 10: по продажам ⌈2 × (45 + 7)⌉ − 40 = 64; Ozon советует 20 — везём
    64 (рекомендация Ozon — нижняя планка). Хватит на 15 дней — плановая; после поставки — (30 + 10 + 64) / 2 = 52 дня."""
    r = cluster_recommend(cpoint(30, "2", 20, transit=10))
    assert (r.to_order, r.own_need, r.ozon_recommended, r.basis, r.min_applied) == (64, 64, 20, SALES, False)
    assert (r.days_left, r.days_after, r.urgency, r.need, r.trend) == (Decimal("15.0"), Decimal("52.0"), PLANNED, 104, None)


def test_cluster_ozon_floor():
    """По продажам почти не нужно (0,1 в день: ⌈5,2⌉ = 6, лежит 5 и едет 1), но Ozon советует 8 — везём 8: это
    необходимый минимум."""
    r = cluster_recommend(cpoint(5, "0.1", 8, transit=1))
    assert (r.to_order, r.own_need, r.basis) == (8, 0, OZON)
    assert cluster_recommend(cpoint(5, "0.1", 0, transit=1)) is None  # Ozon не советует, запаса хватает — не везём
    assert cluster_recommend(cpoint(500, "2", 0)) is None  # затоварен — не везём


def test_cluster_minimum_per_warehouse():
    """Меньше коробки (6 шт.) не возим: Ozon советует 3 — довозим до 6; по продажам 3 и продаётся меньше 6 за весь срок
    (0,05 × 52 = 2,6) — не везём; продаётся достаточно — довозим до 6."""
    r = cluster_recommend(cpoint(0, None, 3, attention="HIGH"))
    assert (r.to_order, r.min_applied, r.basis, r.days_left, r.urgency) == (6, True, OZON, None, URGENT)
    assert cluster_recommend(cpoint(0, "0.05", 0)) is None
    r = cluster_recommend(cpoint(50, "1", None))  # 52 − 50 = 2 по продажам, рекомендаций Ozon нет
    assert (r.to_order, r.min_applied, r.ozon_recommended) == (6, True, None)
    assert cluster_recommend(cpoint(0, None, 3), min_qty=1).to_order == 3  # минимум — настройка


def test_cluster_urgency_and_params():
    r = cluster_recommend(cpoint(0, "1", 0, transit=20))
    assert (r.urgency, r.days_left, r.arrives_late, r.to_order) == (CRITICAL, Decimal("0.0"), True, 32)
    r = cluster_recommend(cpoint(10, "1", 0), Params(target_days=30, lead_time_days=0, safety_days=5))
    assert (r.need, r.to_order, r.urgency) == (35, 25, URGENT)  # 10 дней — «скоро»


# ── API ──
TH, ACB, RS, CM = "6001-2RS.C3.SRL.NAC.TH", "ACB305523.NAC.JP", "6000-2RS.C3.SRL.NAC.JP", "6000.CM.NAC.JP"
URL = "/api/v1/supply/recommendations"
EKB, SPB, KRD, NSK = 154, 2, 999, 777  # кластеры размещения Ozon (macrolocal_cluster_id), как в cluster_list
# Остатки после синхронизации (test_sync_stocks): TH — Екатеринбург 9, СПб 10 (в пути 4), свой склад 12 (резерв 2);
# ACB — СПб 5 (в пути 1); RS — Краснодар 0 (в пути 20); CM — свой склад 1. Продажи Ozon в кластере (ads_cluster, в той же
# выгрузке): TH — Екатеринбург 2 в день, СПб 0,2; ACB — СПб 0,1; RS — Краснодар 1.


class Orders:
    """Заказы своего склада (FBS) — закупка считается по ним, как в 5.2."""

    def __init__(self, sessions, user_id: uuid.UUID) -> None:
        self.sessions, self.user_id, self.n = sessions, user_id, 0
        self.end = today_msk() - timedelta(days=1)

    async def add(self, scheme: str, days_ago: int, qty: dict[str, int], *, cluster: str | None = None,
                  warehouse: int | None = None, status: str = "delivered") -> None:
        async with self.sessions() as db:
            ids = dict((await db.execute(select(Product.offer_id, Product.id).where(Product.user_id == self.user_id))).all())
            self.n += 1
            day = self.end - timedelta(days=days_ago)
            posting = Posting(user_id=self.user_id, scheme=scheme, posting_number=f"T-{self.n}", status=status,
                              ordered_at=datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc), day=day,
                              warehouse_id=warehouse, cluster_to=cluster, synced_at=datetime.now(timezone.utc))
            posting.items = [PostingItem(offer_id=o, product_id=ids.get(o), quantity=q) for o, q in qty.items()]
            db.add(posting)
            await db.commit()

    async def loaded(self, scheme: str, days: int = 90) -> None:
        async with self.sessions() as db:
            db.add(PostingSync(user_id=self.user_id, scheme=scheme, loaded_from=today_msk() - timedelta(days=days - 1),
                               synced_at=datetime.now(timezone.utc)))
            await db.commit()

    async def ozon_recs(self, recs: list[tuple[str, int, int, str | None]], names: dict[int, str]) -> None:
        """Рекомендованная поставка Ozon: (артикул, кластер, шт., внимание) — как после загрузки 10.18a."""
        now = datetime.now(timezone.utc)
        async with self.sessions() as db:
            products = {p.offer_id: p for p in (await db.execute(
                select(Product).where(Product.user_id == self.user_id))).scalars()}
            for offer, cluster, qty, attention in recs:
                p = products[offer]
                db.add(OzonSupplyRec(user_id=self.user_id, product_id=p.id, sku=p.ozon_sku, macrolocal_cluster_id=cluster,
                                     cluster_name=names.get(cluster), recommended=qty, attention_level=attention,
                                     reasons=[{"reason": "NO_SUPPLIES_TO_CLUSTER", "quantity": 3, "amount": "120.00"}]
                                     if cluster == NSK else None, snapshot_at=now))
            db.add(OzonSupplyRecSync(user_id=self.user_id, synced_at=now, attempted_at=now, ok=True, items=len(recs)))
            await db.commit()

    async def metric(self, offer: str, cluster_id: int, macro: int, name: str, ads: str) -> None:
        async with self.sessions() as db:
            pid = (await db.execute(select(Product.id).where(Product.user_id == self.user_id,
                                                             Product.offer_id == offer))).scalar_one()
            db.add(ClusterStockMetric(user_id=self.user_id, product_id=pid, cluster_id=cluster_id,
                                      macrolocal_cluster_id=macro, cluster_name=name, ads=Decimal(ads),
                                      snapshot_at=datetime.now(timezone.utc)))
            await db.commit()


@pytest.fixture
async def seller(sessions, client, tokens, stocks_ozon):  # noqa: F811
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    orders = Orders(sessions, user_id)
    # Ozon советует: TH в Екатеринбург 30 (по продажам больше — 95), ACB в СПб 8 (по продажам не нужно — везём 8),
    # ACB в Новосибирск 3 (склада там нет — до коробки 6), RS в Краснодар 40 (по продажам 32 — берём 40)
    await orders.ozon_recs([(TH, EKB, 30, None), (ACB, SPB, 8, None), (ACB, NSK, 3, "HIGH"), (RS, KRD, 40, None)],
                           {NSK: "Новосибирск", KRD: "Краснодар"})
    # CM продаётся в Екатеринбурге 0,05 в день: за 52 дня — 2,6 шт., меньше коробки — не везём
    await orders.metric(CM, 154, EKB, "Екатеринбург", "0.05")
    # свой склад: TH 9 шт. за 30 дней, 3 за неделю; свободно 12 − 2 = 10 — закупка по заказам, как в 5.2
    await orders.loaded("FBS")
    await orders.add("FBS", 1, {TH: 3}, warehouse=FBS_WH, status="awaiting_packaging")
    await orders.add("FBS", 9, {TH: 6}, warehouse=FBS_WH)
    return orders


def rows(data) -> list[tuple]:
    return [(i["product"]["offer_id"], i["kind"], i["destination"]["name"], i["urgency"], i["days_left"], i["to_order"])
            for i in data["items"]]


def item(data, offer, cluster) -> dict:
    return next(i for i in data["items"] if i["product"]["offer_id"] == offer and i["destination"]["name"] == cluster)


async def test_requires_auth(client):
    assert (await client.get(URL)).status_code == 401
    assert (await client.get("/api/v1/supply/export")).status_code == 401


async def test_recommendations(client, tokens, seller):
    data = (await client.get(URL, headers=auth(tokens))).json()
    assert data["params"] == {"target_days": 45, "lead_time_days": 7, "safety_days": 0} == data["saved"] and data["is_saved"] is True
    assert data["ozon_data"]["clusters_known"] is True and data["ozon_data"]["recs_known"] is True
    assert data["min_qty"] == 6
    assert rows(data) == [
        (RS, "supply", "Краснодар", "critical", "0.0", 40),  # по продажам ⌈1 × 52⌉ − 20 в пути = 32, Ozon — 40
        (TH, "supply", "Екатеринбург", "critical", "4.5", 95),  # 9 / 2 в день; ⌈2 × 52⌉ − 9 = 95, Ozon — 30
        (ACB, "supply", "Новосибирск", "urgent", None, 6),  # Ozon — 3, склада нет — до коробки
        (TH, "purchase", "Склад Подольск", "planned", "23.3", 6),  # свой склад — по заказам, как в 5.2
        (ACB, "supply", "Санкт-Петербург и СЗО", "planned", "50.0", 8),  # по продажам 0, Ozon — 8
    ]
    th = item(data, TH, "Екатеринбург")
    assert (th["avg_daily"], th["sold_30"], th["stock"], th["in_transit"], th["need"]) == ("2.000", None, 9, 0, 104)
    assert (th["ozon_recommended"], th["own_need"], th["basis"], th["days_after"]) == (30, 95, "sales", "52.0")
    assert th["destination"]["warehouses"] == ["ЕКАТЕРИНБУРГ_РФЦ_НОВЫЙ"] and th["ozon_grade"] == "DEFICIT"
    rs = item(data, RS, "Краснодар")
    assert (rs["basis"], rs["own_need"], rs["ozon_recommended"], rs["stock"], rs["in_transit"]) == ("ozon", 32, 40, 0, 20)
    nsk = item(data, ACB, "Новосибирск")
    assert (nsk["min_applied"], nsk["ozon_recommended"], nsk["own_need"], nsk["destination"]["warehouses"]) == (
        True, 3, None, [])
    assert nsk["ozon_reasons"] == ["в кластер не возили — заказы едут издалека"]
    # TH в СПб: продаётся 0,2 в день, лежит 10 и едет 4 — запаса хватает, Ozon не советует — не везём; CM — меньше коробки
    assert not any(i["product"]["offer_id"] == TH and i["destination"]["name"].startswith("Санкт") for i in data["items"])
    assert not any(i["product"]["offer_id"] == CM for i in data["items"])
    assert data["totals"] == {"critical": 2, "urgent": 1, "planned": 2, "supply_units": 149, "purchase_units": 6,
                              "products": 3}


async def test_preferred_warehouse_and_minimum(client, tokens, seller):
    """Склад кластера (у владельца — СПб → Бугры) и минимум на склад — настройки."""
    r = await client.patch("/api/v1/settings", json={"supply_cluster_warehouses": {"санкт-петербург и сзо": 1020000000000202},
                                                    "supply_min_qty": 9}, headers=auth(tokens))
    assert (r.json()["supply_cluster_warehouses"], r.json()["supply_min_qty"]) == (
        {"санкт-петербург и сзо": 1020000000000202}, 9)
    data = (await client.get(URL, headers=auth(tokens))).json()
    spb = item(data, ACB, "Санкт-Петербург и СЗО")
    assert spb["destination"]["warehouse"] == {"id": 1020000000000202, "name": "СПБ_БУГРЫ_РФЦ"}
    assert (spb["to_order"], spb["min_applied"]) == (9, True)  # Ozon советует 8 — до минимума 9
    assert item(data, TH, "Екатеринбург")["destination"]["warehouse"] is None  # не задан — Ozon выберет сам
    r = await client.patch("/api/v1/settings", json={"supply_cluster_warehouses": {}}, headers=auth(tokens))
    assert r.json()["supply_cluster_warehouses"] == {}


async def test_cluster_choices(client, tokens, seller):
    """Кластеры с нашими складами — для настройки «склад кластера»: Новосибирск (только рекомендация Ozon) — нет."""
    r = await client.get("/api/v1/supply/clusters", headers=auth(tokens))
    assert r.status_code == 200
    assert [(c["key"], [w["name"] for w in c["warehouses"]], c["preferred_id"]) for c in r.json()] == [
        ("екатеринбург", ["ЕКАТЕРИНБУРГ_РФЦ_НОВЫЙ"], None),
        ("краснодар", ["КРАСНОДАР_2_РФЦ"], None),
        ("санкт-петербург и сзо", ["СПБ_БУГРЫ_РФЦ"], None),
    ]
    assert (await client.get("/api/v1/supply/clusters")).status_code == 401


async def test_parameters_override_and_save(client, tokens, seller):
    data = (await client.get(URL, params={"target_days": 30, "lead_time_days": 0}, headers=auth(tokens))).json()
    assert data["is_saved"] is False
    th = item(data, TH, "Екатеринбург")
    assert (th["need"], th["to_order"], th["arrives_late"]) == (60, 51, False)
    r = await client.patch("/api/v1/settings", json={"supply_target_days": 30, "supply_lead_time_days": 0},
                           headers=auth(tokens))
    assert (r.json()["supply_target_days"], r.json()["supply_lead_time_days"]) == (30, 0)
    data = (await client.get(URL, headers=auth(tokens))).json()
    assert data["is_saved"] is True and data["params"] == {"target_days": 30, "lead_time_days": 0, "safety_days": 0}
    # срочность — по порогам остатков пользователя (5.1)
    await client.patch("/api/v1/settings", json={"stock_critical_days": 2, "stock_low_days": 5}, headers=auth(tokens))
    data = (await client.get(URL, headers=auth(tokens))).json()
    assert item(data, TH, "Екатеринбург")["urgency"] == "urgent"

    r = await client.get(URL, params={"target_days": 0}, headers=auth(tokens))
    assert r.status_code == 400 and r.json()["detail"].startswith("Запас после поставки")


async def test_without_ozon_recommendations(sessions, client, tokens, stocks_ozon):  # noqa: F811
    """Рекомендации Ozon не загружались (или метод не ответил) — считаем по продажам Ozon в кластере, «Ozon» — нет данных."""
    user_id = await api_user(sessions, client, tokens, stocks_ozon)
    data = (await client.get(URL, headers=auth(tokens))).json()
    assert data["ozon_data"]["recs_known"] is False and data["ozon_data"]["clusters_known"] is True
    assert rows(data) == [
        (RS, "supply", "Краснодар", "critical", "0.0", 32),
        (TH, "supply", "Екатеринбург", "critical", "4.5", 95),
    ]
    assert item(data, TH, "Екатеринбург")["ozon_recommended"] is None
    from app.services.sync.supply_recs import record_failure

    async with sessions() as db:
        await record_failure(db, user_id, "Ozon не ответил")
    data = (await client.get(URL, headers=auth(tokens))).json()
    assert (data["ozon_data"]["recs_ok"], data["ozon_data"]["recs_error"]) == (False, "Ozon не ответил")


async def test_isolation(client, tokens, seller):
    other = {"email": "other@example.com", "password": "strongpass2"}
    await client.post("/api/v1/auth/register", json=other)
    t2 = (await client.post("/api/v1/auth/login", json=other)).json()
    data = (await client.get(URL, headers=auth(t2))).json()
    assert (data["items"], data["postings_known"], data["has_key"], data["ozon_data"]["clusters_known"]) == (
        [], False, False, False)


async def test_export_xlsx(client, tokens, seller):
    r = await client.get("/api/v1/supply/export", headers=auth(tokens))
    assert r.status_code == 200 and "supply.xlsx" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Поставки", "Параметры"]
    table = [list(row) for row in wb["Поставки"].iter_rows(values_only=True)]
    assert table[0][:9] == ["Тип", "Куда", "Склад", "Срочность", "Артикул", "Название", "Отправить / закупить, шт.",
                            "Рекомендует Ozon, шт.", "По продажам, шт."]
    assert [r[:9] for r in table[1:6]] == [  # кластеры с лучшими продажами — первыми
        ["Поставка в Ozon", "Екатеринбург", "любой склад кластера", "Срочно", TH, None, 95, 30, 95],
        ["Поставка в Ozon", "Краснодар", "любой склад кластера", "Срочно", RS, None, 40, 40, 32],
        ["Поставка в Ozon", "Санкт-Петербург и СЗО", "любой склад кластера", "Плановая", ACB, None, 8, 8, 0],
        ["Поставка в Ozon", "Новосибирск", "любой склад кластера", "Скоро", ACB, None, 6, 3, None],
        ["Закупка на свой склад", "Склад Подольск", None, "Плановая", TH, None, 6, None, None],
    ]
    assert table[-1][0] == "Итого" and table[-1][6] == 155
    notes = [c.value for c in wb["Параметры"]["A"] if c.value]
    assert "Итого: в Ozon — 149 шт., закупить на свои склады — 6 шт." in notes
