from app.models.user import User  # noqa: F401  — регистрирует модель в Base.metadata
from app.models.api_credential import ApiCredential  # noqa: F401
from app.models.product import Product, ProductPrice  # noqa: F401
from app.models.sync_run import SyncRun  # noqa: F401
from app.models.warehouse import ProductStock, Warehouse  # noqa: F401
from app.models.accrual import Accrual, AccrualDay, AccrualType  # noqa: F401
from app.models.promotion import Promotion, PromotionProduct  # noqa: F401
from app.models.report import Report, ReportFile  # noqa: F401
from app.models.user_settings import UserSettings  # noqa: F401
from app.models.product_cost import ProductCost  # noqa: F401
