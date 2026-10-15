"""accrual_types, accruals, accrual_days

Revision ID: 0005_accruals
Revises: 0004_warehouses_stocks
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_accruals"
down_revision = "0004_warehouses_stocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accrual_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name_ru", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accruals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("posting_number", sa.String(64), nullable=True),
        sa.Column("sku", sa.BigInteger(), nullable=True),
        sa.Column("accrual_type_id", sa.Integer(), sa.ForeignKey("accrual_types.id"), nullable=False),
        sa.Column("accrual_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("sync_scope", sa.String(80), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "source", "sync_scope", "line_no", name="uq_accruals_scope_line"),
    )
    op.create_index("ix_accruals_user_date", "accruals", ["user_id", "accrual_date"])
    op.create_index("ix_accruals_user_posting", "accruals", ["user_id", "posting_number"])
    op.create_index("ix_accruals_product_id", "accruals", ["product_id"])

    op.create_table(
        "accrual_days",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entries", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("accrual_days")
    op.drop_index("ix_accruals_product_id", table_name="accruals")
    op.drop_index("ix_accruals_user_posting", table_name="accruals")
    op.drop_index("ix_accruals_user_date", table_name="accruals")
    op.drop_table("accruals")
    op.drop_table("accrual_types")
