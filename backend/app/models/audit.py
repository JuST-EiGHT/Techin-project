import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Чем закончилось действие: applied — Ozon применил; rejected — не прошло наши проверки или Ozon отказал по товару;
# failed — ошибка запроса к Ozon (ничего не изменилось); unknown — Ozon не ответил, могло примениться.
# Запись запроса (middleware): ok — ответ без ошибки, rejected — 4xx, failed — 5xx.
APPLIED, REJECTED, FAILED, UNKNOWN, OK = "applied", "rejected", "failed", "unknown", "ok"

# Что записано: пара товар × акция (вход, выход, цена в акции — шаг 4.3); цена товара (шаги 6.2–6.4, entity_id — id
# изменения в price_changes); изменяющий запрос к API (middleware, шаг 6.1: action — http.<метод>, entity_id — шаблон пути)
PROMOTION_ENTITY, PRICE_ENTITY, HTTP_ENTITY = "promotion_product", "product_price", "http"

# JSONB в Postgres (можно искать внутри), JSON в SQLite тестов
JSONData = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base):
    """Журнал действий (правило 6, BUSINESS_LOGIC §8). Два вида записей:

    - действие в Ozon (вход и выход из акции — шаг 4.3, цена — шаги 6.2–6.4): данные до и после, запрос и ответ Ozon,
      итог; пишет сервис действия вместе с изменением, которое она описывает;
    - изменяющий запрос к API (POST, PUT, PATCH, DELETE вошедшего пользователя; middleware, шаг 6.1): метод, путь,
      HTTP-статус — без тела запроса (там бывают пароли и ключи). С записью действия связан общим request_id.

    Записи не меняются и не удаляются: в API нет ни правки, ни удаления, в Postgres это запрещает триггер
    (миграция 0024).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_user_entity", "user_id", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_log_user_product", "user_id", "product_id", "created_at"),
        Index("ix_audit_log_request", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # promotion.join | promotion.update | promotion.leave | http.post | http.put | http.patch | http.delete
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # promotion_product | product_price | http
    # promotion_product — «<id акции Ozon>:<product_id Ozon>»; http — шаблон пути («/api/v1/costs/{offer_id:path}»)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # applied | rejected | failed | unknown | ok
    before_json: Mapped[dict | None] = mapped_column(JSONData)
    after_json: Mapped[dict | None] = mapped_column(JSONData)
    ozon_request: Mapped[dict | None] = mapped_column(JSONData)  # метод и тело запроса к Ozon (без ключа)
    ozon_response: Mapped[dict | None] = mapped_column(JSONData)
    error: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # один на HTTP-запрос: связывает запрос и действие
    method: Mapped[str | None] = mapped_column(String(10))
    path: Mapped[str | None] = mapped_column(String(500))  # фактический путь, без строки запроса
    http_status: Mapped[int | None] = mapped_column(Integer)  # только у записи запроса
