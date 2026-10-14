import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

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

    @classmethod
    def from_row(cls, r: StockRow) -> "StockResponse":
        p, w, s = r.product, r.warehouse, r.stock
        return cls(
            product=StockProduct(id=p.id, offer_id=p.offer_id, name=p.name, ozon_sku=p.ozon_sku),
            warehouse=StockWarehouse(id=w.id, name=w.name, type=w.type, cluster_name=w.cluster_name),
            present=s.present, reserved=s.reserved, in_transit=s.in_transit, snapshot_at=utc(s.snapshot_at),
            avg_daily_sales=r.avg_daily_sales, days_of_supply=r.days_of_supply, status=r.status,
            product_available=r.product_available,
        )
