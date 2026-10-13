from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Theme = Literal["dark", "light", "system"]
TaxBase = Literal["profit", "revenue"]


class SettingsResponse(BaseModel):
    theme: Theme
    vat_multiplier: Decimal  # коэффициент НДС к себестоимости, 1.22
    tax_base: TaxBase  # налог от прибыли или от дохода
    tax_rate: Decimal  # доля: 0.20 = 20 %


class SettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    theme: Theme | None = None
    vat_multiplier: Decimal | None = Field(None, ge=1, le=2, decimal_places=3)
    tax_base: TaxBase | None = None
    tax_rate: Decimal | None = Field(None, ge=0, lt=1, decimal_places=4)
