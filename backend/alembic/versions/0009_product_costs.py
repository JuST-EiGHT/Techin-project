"""product_costs — справочник себестоимости с историей

Revision ID: 0009_product_costs
Revises: 0008_user_settings
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_product_costs"
down_revision = "0008_user_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_costs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_id", sa.String(100), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_multiplier", sa.Numeric(5, 3), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_product_costs_user_offer", "product_costs", ["user_id", "offer_id", "effective_from"])


def downgrade() -> None:
    op.drop_index("ix_product_costs_user_offer", table_name="product_costs")
    op.drop_table("product_costs")
