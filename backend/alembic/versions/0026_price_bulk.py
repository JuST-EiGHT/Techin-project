"""Массовое изменение цен (шаг 6.3): предпросмотры одного массового изменения — общий batch_id, номер запроса в Ozon
(по 100 товаров) — batch_no

Revision ID: 0026_price_bulk
Revises: 0025_price_change_preview
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_price_bulk"
down_revision = "0025_price_change_preview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_changes", sa.Column("batch_id", sa.Uuid()))
    op.add_column("price_changes", sa.Column("batch_no", sa.Integer()))
    op.create_index("ix_price_changes_batch", "price_changes", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_price_changes_batch", table_name="price_changes")
    op.drop_column("price_changes", "batch_no")
    op.drop_column("price_changes", "batch_id")
