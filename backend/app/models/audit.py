import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Чем закончилось действие: applied — Ozon применил; rejected — не прошло наши проверки или Ozon отказал по товару;
# failed — ошибка запроса к Ozon (ничего не изменилось); unknown — Ozon не ответил, могло примениться
APPLIED, REJECTED, FAILED, UNKNOWN = "applied", "rejected", "failed", "unknown"


class AuditLog(Base):
    """Журнал действий, которые меняют данные в Ozon (правило 6, BUSINESS_LOGIC §8). Минимальный — с шага 4.3
    (вход и выход из акций); шаг 6.1 добавит запись всех изменяющих запросов и изменения цен.

    Записи не меняются и не удаляются: в API нет ни правки, ни удаления.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_user_entity", "user_id", "entity_type", "entity_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # promotion.join | promotion.update | promotion.leave
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # promotion_product
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)  # «<id акции Ozon>:<product_id Ozon>»
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # applied | rejected | failed | unknown
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    ozon_request: Mapped[dict | None] = mapped_column(JSON)  # метод и тело запроса к Ozon (без ключа)
    ozon_response: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
