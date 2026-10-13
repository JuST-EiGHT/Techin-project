import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

THEMES = ("dark", "light", "system")
TAX_BASES = ("profit", "revenue")  # налог от прибыли (BUSINESS_LOGIC §3) или от дохода

DEFAULT_THEME = "dark"
DEFAULT_VAT_MULTIPLIER = Decimal("1.22")
DEFAULT_TAX_BASE = "profit"
DEFAULT_TAX_RATE = Decimal("0.20")


class UserSettings(Base):
    """Основные настройки сервиса пользователя (шаг 0.5). Нет строки — значения по умолчанию.

    Расчёты по умолчанию — свои у каждого продавца (правило 13): новые отчёты берут их отсюда.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    theme: Mapped[str] = mapped_column(String(8), nullable=False, default=DEFAULT_THEME)
    vat_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False, default=DEFAULT_VAT_MULTIPLIER)
    tax_base: Mapped[str] = mapped_column(String(8), nullable=False, default=DEFAULT_TAX_BASE)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=DEFAULT_TAX_RATE)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
