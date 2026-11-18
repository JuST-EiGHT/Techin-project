import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrafficLink(Base):
    """Ссылка «Внешнего трафика» с UTM-метками (шаг 11.2). Версии одной ссылки (`link_id`, `version`): правка — новая
    строка, прежняя получает `superseded_at`; удаление — `deleted_at` у действующей строки, «Вернуть» — новая версия.
    Записи не удаляются и не перезаписываются (правило 12). Действующая версия — без `superseded_at`."""

    __tablename__ = "traffic_links"
    __table_args__ = (
        Index("ix_traffic_links_user_current", "user_id", "superseded_at"),
        Index("ix_traffic_links_link", "link_id", "version", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    link_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # одно «Собрать ссылки» — один batch
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)  # что размечено — без меток, свои параметры сохранены
    section: Mapped[str] = mapped_column(String(16), nullable=False)  # product | seller | brand | highlight
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    campaign: Mapped[str] = mapped_column(String(100), nullable=False)  # utm_campaign целиком, с префиксом
    campaign_name: Mapped[str] = mapped_column(String(64), nullable=False)  # после префикса — для фильтров и правки
    utm_source: Mapped[str] = mapped_column(String(200), nullable=False)
    utm_medium: Mapped[str] = mapped_column(String(200), nullable=False)
    utm_term: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    utm_content: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    url: Mapped[str] = mapped_column(Text, nullable=False)  # ссылка с метками
    # из кабинета Ozon (сервис их не создаёт): диплинк — https://ozon.onelink.me/…, перестаёт работать через 30 дней без
    # переходов; с какого момента он у ссылки — для отсчёта, пока нет переходов из отчётов (11.3). Подменный артикул — O-…
    deeplink: Mapped[str | None] = mapped_column(Text)
    deeplink_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    substitute_code: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrafficLinkOpen(Base):
    """«Открыть ссылку» в сервисе по ссылке с диплинком: переход продлевает её ещё на 30 дней. Только добавление."""

    __tablename__ = "traffic_link_opens"
    __table_args__ = (Index("ix_traffic_link_opens_link", "user_id", "link_id", "opened_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    link_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
