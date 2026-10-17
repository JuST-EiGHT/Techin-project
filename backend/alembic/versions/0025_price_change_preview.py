"""Изменение цены одного товара (шаг 6.2): попытка применения ссылается на свой предпросмотр — один предпросмотр
применяется один раз

Revision ID: 0025_price_change_preview
Revises: 0024_audit_price_safety
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_price_change_preview"
down_revision = "0024_audit_price_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_changes", sa.Column("preview_id", sa.Uuid(), sa.ForeignKey("price_changes.id")))
    op.create_index(
        "uq_price_changes_preview", "price_changes", ["preview_id"], unique=True,
        postgresql_where=sa.text("preview_id IS NOT NULL"), sqlite_where=sa.text("preview_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_price_changes_preview", table_name="price_changes")
    op.drop_column("price_changes", "preview_id")
