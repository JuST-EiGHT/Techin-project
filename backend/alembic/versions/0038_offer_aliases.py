"""Себестоимость (шаг 10.15): сопоставление артикулов файла с товарами Ozon

offer_aliases — «артикул в файле → артикул в Ozon», запоминается при привязке в загрузке себестоимости. Отвязка и
перепривязка — removed_at у прежней записи, записи не удаляются (правило 12).

Revision ID: 0038_offer_aliases
Revises: 0037_ozon_documents
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_offer_aliases"
down_revision = "0037_ozon_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offer_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("alias_key", sa.String(100), nullable=False),
        sa.Column("offer_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offer_aliases_user_key", "offer_aliases", ["user_id", "alias_key"])


def downgrade() -> None:
    op.drop_index("ix_offer_aliases_user_key", table_name="offer_aliases")
    op.drop_table("offer_aliases")
