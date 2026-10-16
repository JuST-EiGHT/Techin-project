from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Theme = Literal["dark", "light", "system"]
# «Откуда товар»: при закупке, после продажи, с отсрочкой, своё производство, по-разному
SupplierPayment = Literal["upfront", "after_sale", "deferred", "own_production", "mixed"]


class SettingsResponse(BaseModel):
    """Налог и НДС — в налоговом профиле (/api/v1/tax-profile, шаг 2.5)."""

    theme: Theme
    supplier_payment: SupplierPayment
    supplier_deferral_days: int | None = None  # отсрочка оплаты поставщику, дней — у deferred
    cost_label: str  # как называется себестоимость в P&L: «Себестоимость с НДС» / «Долг поставщику» / «… производства»
    price_ad_pct: Decimal  # калькулятор цены: реклама, % от цены
    price_target_margin_pct: Decimal  # целевая маржа, %
    price_defaults: bool  # реклама и маржа ещё не заданы — показаны значения по умолчанию
    # пороги статуса остатка, дней запаса (шаг 5.1): меньше critical — «Критично», меньше low — «Мало»,
    # больше excess — «Избыток»
    stock_critical_days: int
    stock_low_days: int
    stock_excess_days: int


class SettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    theme: Theme | None = None
    supplier_payment: SupplierPayment | None = None
    supplier_deferral_days: int | None = Field(None, ge=1, le=365)
    price_ad_pct: Decimal | None = Field(None, ge=0, le=90)
    price_target_margin_pct: Decimal | None = Field(None, ge=0, le=90)
    stock_critical_days: int | None = Field(None, ge=1, le=365)
    stock_low_days: int | None = Field(None, ge=1, le=365)
    stock_excess_days: int | None = Field(None, ge=1, le=365)
