"""Журнал действий, которые меняют данные в Ozon (audit_log): только добавление, без правки и удаления."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import APPLIED, AuditLog
from app.schemas.sync import utc


def client_info(request: Request | None) -> tuple[str | None, str | None]:
    """IP (первый из X-Forwarded-For — запрос приходит через прокси Next) и User-Agent."""
    if request is None:
        return None, None
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else None)
    return (ip or None), (request.headers.get("user-agent") or None)


def record(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    status: str,
    before: dict | None = None,
    after: dict | None = None,
    ozon_request: dict | None = None,
    ozon_response: dict | None = None,
    error: str | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Новая запись журнала. Коммит — на вызывающем (вместе с изменением, которое она описывает)."""
    ip, agent = client_info(request)
    entry = AuditLog(
        user_id=user_id, created_at=datetime.now(timezone.utc), action=action, entity_type=entity_type,
        entity_id=entity_id, status=status, before_json=before, after_json=after, ozon_request=ozon_request,
        ozon_response=ozon_response, error=error, ip=ip, user_agent=agent[:500] if agent else None,
    )
    db.add(entry)
    return entry


async def last_applied(
    db: AsyncSession, user_id: uuid.UUID, entity_type: str, entity_id: str, actions: tuple[str, ...], within: timedelta
) -> datetime | None:
    """Когда последний раз применили одно из действий к этой сущности — если за последние `within`."""
    since = datetime.now(timezone.utc) - within
    found = await db.scalar(
        select(AuditLog.created_at)
        .where(
            AuditLog.user_id == user_id, AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id,
            AuditLog.action.in_(actions), AuditLog.status == APPLIED, AuditLog.created_at >= since,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    return utc(found)
