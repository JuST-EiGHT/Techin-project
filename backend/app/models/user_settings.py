import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

THEMES = ("dark", "light", "system")
DEFAULT_THEME = "dark"
# когда поставщик получает деньги: upfront — при закупке, after_sale — после продажи товара
SUPPLIER_PAYMENTS = ("upfront", "after_sale")
DEFAULT_SUPPLIER_PAYMENT = "upfront"
COST_LABELS = {"upfront": "Себестоимость с НДС", "after_sale": "Долг поставщику"}


class UserSettings(Base):
    """Основные настройки сервиса пользователя (шаг 0.5). Нет строки — значения по умолчанию.

    Налог и НДС — не здесь, а в налоговом профиле (`tax_profiles`, шаг 2.5).
    """

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    theme: Mapped[str] = mapped_column(String(8), nullable=False, default=DEFAULT_THEME)
    # подпись себестоимости с НДС в P&L (шаг 3.3); дальше — «Откуда товар» с отсрочкой и своим производством (3.8)
    # и генератор оплаты поставщику в платёжном календаре (3.9)
    supplier_payment: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFAULT_SUPPLIER_PAYMENT, server_default=DEFAULT_SUPPLIER_PAYMENT
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
