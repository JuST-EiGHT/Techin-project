"""Основные настройки сервиса: тема, «Откуда товар», расчёт цены. Налог и НДС — в налоговом профиле (tax_profile.py)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.models.user_settings import COST_LABELS, UserSettings
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services import user_settings as svc
from app.services.auth import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])


def _response(s: UserSettings) -> SettingsResponse:
    ad, margin = svc.price_settings(s)
    return SettingsResponse(
        theme=s.theme, supplier_payment=s.supplier_payment, supplier_deferral_days=s.supplier_deferral_days,
        cost_label=COST_LABELS[s.supplier_payment], price_ad_pct=ad, price_target_margin_pct=margin,
        price_defaults=s.price_ad_pct is None and s.price_target_margin_pct is None,
    )


@router.get("", response_model=SettingsResponse)
async def get_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return _response(await svc.get_settings(db, user.id))


@router.patch("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    try:
        return _response(await svc.update_settings(db, user.id, changes))
    except svc.SettingsError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
