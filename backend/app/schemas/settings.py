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


class SettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    theme: Theme | None = None
    supplier_payment: SupplierPayment | None = None
    supplier_deferral_days: int | None = Field(None, ge=1, le=365)
