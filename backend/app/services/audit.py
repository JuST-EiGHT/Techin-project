"""Журнал действий (audit_log): только добавление, без правки и удаления.

Действия в Ozon записывает сервис действия (`record`), изменяющие запросы к API — middleware
(`app/core/audit_middleware.py`). Запрос, который ничего не меняет (предпросмотр, расчёт, файл ошибок), помечается
`not_a_change(request)` или зависимостью `read_only` — тогда middleware его не пишет.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import (
    APPLIED,
    FAILED,
    HTTP_ENTITY,
    PRICE_ENTITY,
    PROMOTION_ENTITY,
    REJECTED,
    UNKNOWN,
    AuditLog,
)
from app.models.product import Product
from app.schemas.sync import utc
from app.services.promotion_history import bounds

REQUEST_ID = "audit_request_id"  # ключ в request.state: id запроса, его ставит middleware
SKIP = "audit_skip"  # ключ в request.state: запрос ничего не меняет — не писать


def client_info(request: Request | None) -> tuple[str | None, str | None]:
    """IP (первый из X-Forwarded-For — запрос приходит через прокси Next) и User-Agent."""
    if request is None:
        return None, None
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else None)
    return (ip or None), (request.headers.get("user-agent") or None)


def request_id(request: Request | None) -> uuid.UUID | None:
    return getattr(request.state, REQUEST_ID, None) if request is not None else None


def not_a_change(request: Request) -> None:
    """Этот запрос ничего не меняет (предпросмотр, dry-run) — в журнал запросов не писать."""
    setattr(request.state, SKIP, True)


async def read_only(request: Request) -> None:
    """Зависимость для эндпоинтов, которые принимают POST, но ничего не меняют (расчёт, разбор файла)."""
    not_a_change(request)


def record(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    status: str,
    product_id: uuid.UUID | None = None,
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
        entity_id=entity_id, product_id=product_id, status=status, before_json=before, after_json=after,
        ozon_request=ozon_request, ozon_response=ozon_response, error=error, ip=ip,
        user_agent=agent[:500] if agent else None, request_id=request_id(request),
        method=request.method if request is not None else None,
        path=request.url.path[:500] if request is not None else None,
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


# ── журнал изменений цен: GET /audit/prices ──
KINDS = {"price": PRICE_ENTITY, "promotion": PROMOTION_ENTITY}
KIND_OF = {entity: kind for kind, entity in KINDS.items()}
STATUSES = (APPLIED, REJECTED, FAILED, UNKNOWN)
STATUS_TITLES = {
    APPLIED: "Применено",
    REJECTED: "Отклонено: не прошло проверки или Ozon отказал",
    FAILED: "Ошибка запроса к Ozon — ничего не изменилось",
    UNKNOWN: "Ozon не ответил — изменение могло примениться",
}
ACTION_TITLES = {
    "promotion.join": "Вход в акцию",
    "promotion.update": "Цена или количество в акции",
    "promotion.leave": "Выход из акции",
    "price.update": "Изменение цены",
}


async def price_log(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, *, product_id: uuid.UUID | None = None,
    kind: str | None = None, status: str | None = None, action: str | None = None, limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """Действия с ценами за период (дни по Москве), новые первыми: цена товара и цена в акциях. Записи запросов к API
    сюда не входят — из них берётся только HTTP-статус ответа (по request_id)."""
    start, end = bounds(date_from, date_to)
    cond = [
        AuditLog.user_id == user_id, AuditLog.created_at >= start, AuditLog.created_at < end,
        AuditLog.entity_type.in_([KINDS[kind]] if kind else list(KINDS.values())),
    ]
    if product_id is not None:
        cond.append(AuditLog.product_id == product_id)
    if status is not None:
        cond.append(AuditLog.status == status)
    if action is not None:
        cond.append(AuditLog.action == action)
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*cond)) or 0
    rows = (await db.execute(
        select(AuditLog, Product).outerjoin(Product, Product.id == AuditLog.product_id).where(*cond)
        .order_by(AuditLog.created_at.desc(), AuditLog.id).offset(offset).limit(limit)
    )).all()
    rids = {e.request_id for e, _ in rows if e.request_id is not None}
    codes: dict[uuid.UUID, int | None] = {}
    if rids:
        codes = dict((await db.execute(
            select(AuditLog.request_id, AuditLog.http_status).where(
                AuditLog.user_id == user_id, AuditLog.entity_type == HTTP_ENTITY, AuditLog.request_id.in_(rids)
            )
        )).all())
    items = [
        {
            "id": e.id, "created_at": utc(e.created_at), "kind": KIND_OF[e.entity_type], "action": e.action,
            "action_title": ACTION_TITLES.get(e.action, e.action), "status": e.status,
            "status_title": STATUS_TITLES.get(e.status, e.status), "entity_id": e.entity_id,
            "product": {"id": p.id, "offer_id": p.offer_id, "name": p.name} if p is not None else None,
            "before": e.before_json, "after": e.after_json, "ozon_request": e.ozon_request,
            "ozon_response": e.ozon_response, "error": e.error, "ip": e.ip, "user_agent": e.user_agent,
            "request": {"id": e.request_id, "method": e.method, "path": e.path, "http_status": codes.get(e.request_id)}
            if e.method else None,
        }
        for e, p in rows
    ]
    return items, total
