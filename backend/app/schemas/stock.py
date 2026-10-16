import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.sync import utc
from app.services.stocks import StockRow


class WarehouseResponse(BaseModel):
    id: uuid.UUID
    ozon_warehouse_id: int
    name: str
    type: str  # FBO | FBS | rFBS
    cluster_name: str | None
    is_active: bool
    synced_at: datetime
    products_in_stock: int  # товаров с ненулевым остатком
    total_present: int
    total_in_transit: int


class StockProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None
    ozon_sku: int | None


class StockWarehouse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    cluster_name: str | None


class StockResponse(BaseModel):
    product: StockProduct
    warehouse: StockWarehouse
    present: int
    reserved: int | None
    in_transit: int | None
    snapshot_at: datetime
    avg_daily_sales: Decimal | None  # по товару в целом за 30 дней; null — мало данных (шаг 3.5)
    days_of_supply: Decimal | None  # весь остаток товара на всех складах / продажи в день
    product_available: int  # доступно к продаже на всех складах
    status: str | None  # critical | low | normal | excess | null (нет данных о продажах)
    last_sale: date | None = None  # последний день с продажей товара; null — продаж в данных нет
    days_without_sales: int | None = None

    @classmethod
    def from_row(cls, r: StockRow) -> "StockResponse":
        p, w, s = r.product, r.warehouse, r.stock
        return cls(
            product=StockProduct(id=p.id, offer_id=p.offer_id, name=p.name, ozon_sku=p.ozon_sku),
            warehouse=StockWarehouse(id=w.id, name=w.name, type=w.type, cluster_name=w.cluster_name),
            present=s.present, reserved=s.reserved, in_transit=s.in_transit, snapshot_at=utc(s.snapshot_at),
            avg_daily_sales=r.avg_daily_sales, days_of_supply=r.days_of_supply, status=r.status,
            product_available=r.product_available, last_sale=r.last_sale, days_without_sales=r.days_without_sales,
        )


# ── сводка по складам (шаг 5.1) ──
class Thresholds(BaseModel):
    critical: int  # меньше — «Критично», дней запаса
    low: int  # меньше — «Мало»
    excess: int  # больше — «Избыток»


class StatusCounts(BaseModel):
    critical: int
    low: int
    normal: int
    excess: int
    no_data: int


class ProductStockOut(BaseModel):
    """Товар в целом: остаток на всех складах, продажи, дни запаса, статус."""

    product: StockProduct
    available: int  # доступно к продаже на всех складах
    in_transit: int | None
    avg_daily_sales: Decimal | None
    days_of_supply: Decimal | None
    status: str | None
    last_sale: date | None
    days_without_sales: int | None  # null — продаж в данных нет
    warehouses: int  # на скольких складах есть остаток


class TopProduct(ProductStockOut):
    present: int  # на этом складе
    in_transit_here: int | None = None


class WarehouseSummary(BaseModel):
    id: uuid.UUID
    ozon_warehouse_id: int
    name: str
    type: str
    cluster_id: int | None
    cluster_name: str | None
    is_active: bool
    synced_at: datetime
    skus: int  # товаров с остатком на складе
    total_present: int
    total_in_transit: int
    counts: StatusCounts  # товары склада по статусу остатка (статус — по товару в целом)
    top: list[TopProduct]  # 5 товаров с самым большим остатком на складе


class Totals(BaseModel):
    products: int
    present: int
    in_transit: int
    counts: StatusCounts


class WarehousesSummary(BaseModel):
    thresholds: Thresholds
    sales_known: bool  # продажи в день посчитаны; иначе статусы — «нет данных»
    stocks_known: bool  # остатки хоть раз загружались
    synced_at: datetime | None
    totals: Totals
    warehouses: list[WarehouseSummary]
    running_out: list[ProductStockOut]  # «Критично» и «Мало», меньше всего дней запаса первыми
    running_out_total: int
    lying: list[ProductStockOut]  # «Избыток»: дольше не продававшиеся первыми
    lying_total: int


class RunningOut(BaseModel):
    """Виджет «Заканчиваются» на главной."""

    thresholds: Thresholds
    sales_known: bool
    stocks_known: bool
    critical: int
    low: int
    items: list[ProductStockOut]



# ── кандидаты на вывоз (шаг 5.1) ──
class WithdrawalParams(BaseModel):
    total: int | None = Field(None, ge=0, le=1_000_000)  # вывезти всего, шт.; null или 0 — без ограничения
    keep_idle: int = Field(2, ge=0, le=10_000)  # оставлять в кластере у товара без продаж
    keep_selling: int = Field(10, ge=0, le=10_000)  # оставлять в кластере у продающегося
    excluded: list[str] = Field(default_factory=list, max_length=200)  # кластеры, которые не трогаем (id кластера)


class ClusterOut(BaseModel):
    key: str  # id кластера Ozon
    name: str
    present: int  # штук товаров в кластере
    products: int


class WithdrawalLine(BaseModel):
    warehouse_id: uuid.UUID
    warehouse: str
    cluster_key: str | None
    cluster: str | None
    present: int  # на складе сейчас
    qty: int  # вывезти
    cluster_left: int  # останется в кластере


class WithdrawalItem(BaseModel):
    product: StockProduct
    kind: str  # idle — не продаётся, selling — продаётся, но запаса слишком много
    reason: str
    keep: int  # оставляем в каждом кластере
    surplus: int | None  # продающийся: остаток сверх запаса на порог «Избыток»
    available: int  # на всех складах
    avg_daily_sales: Decimal | None
    days_of_supply: Decimal | None
    last_sale: date | None
    days_without_sales: int | None
    possible: int  # можно вывезти без лимита
    qty: int  # вывезти с учётом лимита
    excluded_units: int  # лежит в исключённых кластерах
    lines: list[WithdrawalLine]


class WithdrawalReport(BaseModel):
    params: WithdrawalParams  # по которым посчитано
    saved: WithdrawalParams  # сохранённые у пользователя
    is_saved: bool
    thresholds: Thresholds
    sales_known: bool
    stocks_known: bool
    sales_since: date | None  # первый день данных о продажах
    today: date
    clusters: list[ClusterOut]
    possible: int  # можно вывезти всего без лимита
    planned: int  # вывезти с учётом лимита
    left_out: int  # товаров, не вошедших в лимит
    items: list[WithdrawalItem]
