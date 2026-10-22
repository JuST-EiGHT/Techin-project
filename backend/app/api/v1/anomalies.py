"""Аномалии (шаги 7.1–7.2): список, счётчик для колокольчика, «Ок», «Отметить все прочитанными». Ищет их Celery раз в
час (services/anomalies.detect)."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.anomaly_detector import TYPES
from app.models.sync_run import SUCCESS
from app.models.user import User
from app.schemas.anomaly import (
    AcknowledgeAll,
    AcknowledgedAll,
    AnomaliesPage,
    AnomalyCounts,
    AnomalyItem,
    Group,
    Severity,
)
from app.schemas.sync import utc
from app.services import anomalies as svc
from app.services.stores import get_current_store
from app.services.sync.runner import latest_run

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("", response_model=AnomaliesPage)
async def list_anomalies(
    status_: Literal["active", "resolved", "all"] = Query("active", alias="status",
                                                          description="active — сейчас, resolved — закрытые, all — все"),
    severity: Severity | None = Query(None),
    group: Group | None = Query(None, description="sales | price | margin | stock | promo | money"),
    type_: str | None = Query(None, alias="type", description="Тип: sales_drop, price_change …"),
    product_id: uuid.UUID | None = Query(None),
    acknowledged: bool | None = Query(None, description="false — только без «Ок»"),
    order: Literal["severity", "new", "resolved"] | None = Query(
        None, description="severity — красные первыми (по умолчанию у активных), new — новые первыми, resolved — по закрытию"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Аномалии: активные — красные первыми, внутри — новые первыми; закрытые — по времени закрытия. Записи не
    удаляются: закрытая остаётся в истории, повторное появление — новая запись."""
    if type_ is not None and type_ not in TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Тип аномалии: недопустимое значение")
    page = await svc.list_anomalies(
        db, user.id, status=status_, severity=severity, group=group, type_=type_, product_id=product_id,
        acknowledged=acknowledged, order=order, limit=limit, offset=offset,
    )
    run = await latest_run(db, user.id, "anomalies", status=SUCCESS)
    return {**page, "counts": await svc.counts(db, user.id), "checked_at": utc(run.finished_at) if run else None}


@router.get("/count", response_model=AnomalyCounts)
async def count(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Сколько активных аномалий без «Ок» (красных и жёлтых) — для колокольчика в шапке."""
    return await svc.counts(db, user.id)


@router.post("/acknowledge-all", response_model=AcknowledgedAll)
async def acknowledge_all(data: AcknowledgeAll, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Отметить все прочитанными»: «Ок» у всех активных без «Ок» — по фильтрам страницы (без фильтров — у всех)."""
    if data.type is not None and data.type not in TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Тип аномалии: недопустимое значение")
    n = await svc.acknowledge_all(db, user.id, severity=data.severity, group=data.group, type_=data.type,
                                  product_id=data.product_id)
    return {"acknowledged": n, "counts": await svc.counts(db, user.id)}


@router.post("/{anomaly_id}/acknowledge", response_model=AnomalyItem)
async def acknowledge(anomaly_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Ок»: видел. Аномалия остаётся активной, пока метрика не вернётся в норму, но в счётчик больше не входит."""
    try:
        return await svc.acknowledge(db, user.id, anomaly_id)
    except svc.NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аномалия не найдена") from exc
