"""sales_daily — продажи по товарам по дням (производная из начислений)

Revision ID: 0013_sales_daily
Revises: 0012_supplier_payment
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_sales_daily"
down_revision = "0012_supplier_payment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("offer_id", sa.String(100), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("sku", sa.BigInteger()),
        sa.Column("qty_sold", sa.Integer(), nullable=False),
        sa.Column("qty_returned", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), nullable=False),
        sa.Column("returns_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "day", "offer_id", name="uq_sales_daily_day_offer"),
    )
    op.create_index("ix_sales_daily_user_offer_day", "sales_daily", ["user_id", "offer_id", "day"])


def downgrade() -> None:
    op.drop_index("ix_sales_daily_user_offer_day", table_name="sales_daily")
    op.drop_table("sales_daily")
