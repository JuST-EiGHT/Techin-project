"""Основные настройки сервиса: тема и расчёты по умолчанию."""
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services import user_settings as svc
from app.services.auth import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])


def _response(s: UserSettings) -> SettingsResponse:
    # одинаковый вид чисел для PostgreSQL и SQLite — масштаб колонки
    return SettingsResponse(
        theme=s.theme, tax_base=s.tax_base,
        vat_multiplier=Decimal(s.vat_multiplier).quantize(Decimal("0.001")),
        tax_rate=Decimal(s.tax_rate).quantize(Decimal("0.0001")),
    )


@router.get("", response_model=SettingsResponse)
async def get_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return _response(await svc.get_settings(db, user.id))


@router.patch("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    return _response(await svc.update_settings(db, user.id, changes))
