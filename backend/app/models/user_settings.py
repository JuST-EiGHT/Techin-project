import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

THEMES = ("dark", "light", "system")
DEFAULT_THEME = "dark"
# «Откуда товар» (шаги 3.3, 3.8): upfront — закупаю и плачу при закупке, after_sale — поставщик получает деньги после
# продажи, deferred — закупка с отсрочкой N дней, own_production — своё производство, mixed — по-разному
SUPPLIER_PAYMENTS = ("upfront", "after_sale", "deferred", "own_production", "mixed")
DEFAULT_SUPPLIER_PAYMENT = "upfront"
COST_LABELS = {
    "upfront": "Себестоимость с НДС",
    "after_sale": "Долг поставщику",
    "deferred": "Себестоимость с НДС",
    "own_production": "Себестоимость производства",
    "mixed": "Себестоимость с НДС",
}
MAX_DEFERRAL_DAYS = 365


class UserSettings(Base):
    """Основные настройки сервиса пользователя (шаг 0.5). Нет строки — значения по умолчанию.

    Налог и НДС — не здесь, а в налоговом профиле (`tax_profiles`, шаг 2.5).
    """

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    theme: Mapped[str] = mapped_column(String(8), nullable=False, default=DEFAULT_THEME)
    # «Откуда товар»: подпись себестоимости в P&L (шаг 3.3), статьи планирования (3.8), генератор оплаты
    # поставщику в платёжном календаре (3.9)
    supplier_payment: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFAULT_SUPPLIER_PAYMENT, server_default=DEFAULT_SUPPLIER_PAYMENT
    )
    supplier_deferral_days: Mapped[int | None] = mapped_column(Integer)  # отсрочка оплаты — только у deferred
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
