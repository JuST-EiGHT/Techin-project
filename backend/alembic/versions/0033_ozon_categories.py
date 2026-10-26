"""Справочник категорий Ozon (шаг 10.3): названия категорий товаров для фильтров аналитики

Ozon отдаёт дерево категорий (/v1/description-category/tree); у товара — description_category_id. Храним только
нужные категории с путём от корня. Справочник общий для всех продавцов — без user_id.

Revision ID: 0033_ozon_categories
Revises: 0032_stores
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_ozon_categories"
down_revision = "0032_stores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ozon_categories",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("parent_id", sa.BigInteger()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ozon_categories")
