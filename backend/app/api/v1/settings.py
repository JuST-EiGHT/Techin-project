"""Основные настройки сервиса: тема, «Откуда товар», расчёт цены, пороги остатков, поставки, пороги аномалий и «низкой
маржи». Налог и НДС — в налоговом профиле (tax_profile.py).

Тема — настройка аккаунта (одна на все магазины), остальное — настройки текущего магазина (шаг 8.4b)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.anomaly_detector import DEFAULT_THRESHOLDS
from app.models.user import User
from app.models.user_settings import COST_LABELS, UserSettings
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services import user_settings as svc
from app.services.stores import account_of, get_current_store

router = APIRouter(prefix="/settings", tags=["settings"])


def _response(s: UserSettings, theme: str) -> SettingsResponse:
    ad, margin = svc.price_settings(s)
    t = svc.stock_thresholds(s)
    target, lead = svc.supply_params(s)
    a = svc.anomaly_thresholds(s)
    return SettingsResponse(
        theme=theme, supplier_payment=s.supplier_payment, supplier_deferral_days=s.supplier_deferral_days,
        cost_label=COST_LABELS[s.supplier_payment], price_ad_pct=ad, price_target_margin_pct=margin,
        price_defaults=s.price_ad_pct is None and s.price_target_margin_pct is None,
        stock_critical_days=t.critical, stock_low_days=t.low, stock_excess_days=t.excess,
        supply_target_days=target, supply_lead_time_days=lead, supply_safety_days=svc.supply_safety_days(s),
        supply_demand_days=svc.supply_demand_days(s), min_profit_rub=svc.min_profit(s),
        low_margin_pct=a.low_margin, anomaly_sales_drop_pct=a.sales_drop, anomaly_sales_drop_red_pct=a.sales_drop_red,
        anomaly_sales_growth_pct=a.sales_growth, anomaly_price_pct=a.price, anomaly_price_red_pct=a.price_red,
        anomaly_commission_pp=a.commission_pp, anomaly_payout_diff_pct=a.payout_diff,
        anomaly_cash_gap_days=a.cash_gap_days, anomaly_uncategorized_days=a.uncategorized_days,
        anomaly_sync_stale_hours=a.sync_stale_hours, anomaly_data_gap_days=a.data_gap_days,
        recommendation_hide_days=svc.recommendation_hide_days(s),
        abc_a_pct=int(svc.abc_bounds(s).a), abc_b_pct=int(svc.abc_bounds(s).b),
        anomaly_defaults=all(getattr(a, f) == getattr(DEFAULT_THRESHOLDS, f) for f in svc.ANOMALY_FIELDS.values()),
    )


async def _theme(db: AsyncSession, store: User) -> str:
    account = await account_of(db, store)
    return (await svc.get_settings(db, account.id)).theme


@router.get("", response_model=SettingsResponse)
async def get_settings(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return _response(await svc.get_settings(db, user.id), await _theme(db, user))


@router.patch("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    theme = changes.pop("theme", None)
    try:
        if theme is not None:
            await svc.update_settings(db, user.account_id, {"theme": theme})
        current = await svc.update_settings(db, user.id, changes) if changes else await svc.get_settings(db, user.id)
    except svc.SettingsError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _response(current, await _theme(db, user))
