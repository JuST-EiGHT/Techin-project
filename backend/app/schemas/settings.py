from typing import Literal

from pydantic import BaseModel

Theme = Literal["dark", "light", "system"]
SupplierPayment = Literal["upfront", "after_sale"]


class SettingsResponse(BaseModel):
    """Налог и НДС — в налоговом профиле (/api/v1/tax-profile, шаг 2.5)."""

    theme: Theme
    supplier_payment: SupplierPayment  # upfront — платите поставщику при закупке, after_sale — после продажи
    cost_label: str  # как называется себестоимость с НДС в P&L: «Себестоимость с НДС» / «Долг поставщику»


class SettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    theme: Theme | None = None
    supplier_payment: SupplierPayment | None = None
