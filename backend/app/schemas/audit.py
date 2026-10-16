import uuid
from datetime import date, datetime

from pydantic import BaseModel


class AuditProduct(BaseModel):
    id: uuid.UUID
    offer_id: str
    name: str | None


class AuditRequest(BaseModel):
    """HTTP-запрос, в котором случилось действие (запись middleware с тем же request_id)."""

    id: uuid.UUID | None
    method: str | None
    path: str | None
    http_status: int | None  # что ответил сервис; нет — запись запроса не сохранилась


class AuditEntry(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: str  # price — цена товара; promotion — вход, выход, цена в акции
    action: str  # promotion.join | promotion.update | promotion.leave | … (изменение цены — шаг 6.2)
    action_title: str
    status: str  # applied | rejected | failed | unknown
    status_title: str
    entity_id: str
    product: AuditProduct | None
    before: dict | None
    after: dict | None
    ozon_request: dict | None  # метод, путь и тело запроса к Ozon (без ключа)
    ozon_response: dict | None
    error: str | None
    ip: str | None
    user_agent: str | None
    request: AuditRequest | None


class AuditPage(BaseModel):
    date_from: date
    date_to: date
    total: int
    limit: int
    offset: int
    items: list[AuditEntry]
