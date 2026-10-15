from fastapi import APIRouter

from app.api.v1 import (
    auth,
    costs,
    credentials,
    dashboard,
    finance,
    ozon,
    plan_calendar,
    planning,
    planning_overview,
    pnl,
    promotions,
    reports,
    sales,
    settings,
    stocks,
    sync,
    tax_profile,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(credentials.router)
api_router.include_router(sync.router)
api_router.include_router(stocks.warehouses_router)
api_router.include_router(stocks.stocks_router)
api_router.include_router(finance.router)
api_router.include_router(promotions.router)
api_router.include_router(reports.router)
api_router.include_router(settings.router)
api_router.include_router(costs.router)
api_router.include_router(tax_profile.router)
api_router.include_router(pnl.router)
api_router.include_router(sales.router)
api_router.include_router(ozon.router)
api_router.include_router(dashboard.router)
api_router.include_router(planning.router)
api_router.include_router(plan_calendar.router)
api_router.include_router(planning_overview.router)
