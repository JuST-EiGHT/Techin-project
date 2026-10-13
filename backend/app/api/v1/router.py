from fastapi import APIRouter

from app.api.v1 import auth, credentials, finance, promotions, reports, settings, stocks, sync

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
