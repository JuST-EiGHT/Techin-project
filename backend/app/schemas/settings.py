from typing import Literal

from pydantic import BaseModel

Theme = Literal["dark", "light", "system"]


class SettingsResponse(BaseModel):
    """Налог и НДС — в налоговом профиле (/api/v1/tax-profile, шаг 2.5)."""

    theme: Theme


class SettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    theme: Theme | None = None
