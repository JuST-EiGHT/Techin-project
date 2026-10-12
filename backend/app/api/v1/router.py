from fastapi import APIRouter

from app.api.v1 import auth, credentials, sync

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(credentials.router)
api_router.include_router(sync.router)
