from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OzonCategory(Base):
    """Категория Ozon (шаг 10.3): название по `description_category_id` товара — для фильтра «категория» в аналитике.

    Справочник Ozon общий для всех продавцов, поэтому без user_id. Храним только категории, в которых есть товары
    магазинов сервиса (и их родителей — для пути). Справочник, не исходные данные продавца: обновление перезаписывает
    название, история не нужна (правило 12 — о данных продавца).
    """

    __tablename__ = "ozon_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # description_category_id
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)  # «Автотовары / Подшипники»
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
