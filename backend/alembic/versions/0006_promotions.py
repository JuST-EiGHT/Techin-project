"""promotions, promotion_products

Revision ID: 0006_promotions
Revises: 0005_accruals
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_promotions"
down_revision = "0005_accruals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promotions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ozon_action_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("date_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discount_type", sa.String(32), nullable=True),
        sa.Column("discount_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("freeze_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_add_dates", sa.JSON(), nullable=True),
        sa.Column("is_participating", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("participating_products_count", sa.Integer(), nullable=True),
        sa.Column("potential_products_count", sa.Integer(), nullable=True),
        sa.Column("whole_stock", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "ozon_action_id", name="uq_promotions_user_action"),
    )
    op.create_index("ix_promotions_user_id", "promotions", ["user_id"])

    op.create_table(
        "promotion_products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("promotion_id", sa.Uuid(), sa.ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("max_action_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("added_automatically", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("add_mode", sa.String(32), nullable=True),
        sa.Column("margin_in_promo", sa.Numeric(7, 2), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("promotion_id", "product_id", name="uq_promotion_products_pair"),
    )
    op.create_index("ix_promotion_products_promotion_id", "promotion_products", ["promotion_id"])
    op.create_index("ix_promotion_products_product_id", "promotion_products", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_promotion_products_product_id", table_name="promotion_products")
    op.drop_index("ix_promotion_products_promotion_id", table_name="promotion_products")
    op.drop_table("promotion_products")
    op.drop_index("ix_promotions_user_id", table_name="promotions")
    op.drop_table("promotions")
