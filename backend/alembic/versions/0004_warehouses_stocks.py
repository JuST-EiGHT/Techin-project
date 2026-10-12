"""warehouses, product_stocks

Revision ID: 0004_warehouses_stocks
Revises: 0003_products_prices_sync
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_warehouses_stocks"
down_revision = "0003_products_prices_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ozon_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=True),
        sa.Column("cluster_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "ozon_warehouse_id", name="uq_warehouses_user_warehouse"),
    )
    op.create_index("ix_warehouses_user_id", "warehouses", ["user_id"])

    op.create_table(
        "product_stocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("present", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=True),
        sa.Column("in_transit", sa.Integer(), nullable=True),
    )
    op.create_index("ix_product_stocks_warehouse_id", "product_stocks", ["warehouse_id"])
    op.create_index("ix_product_stocks_pair_snapshot", "product_stocks", ["product_id", "warehouse_id", "snapshot_at"])


def downgrade() -> None:
    op.drop_index("ix_product_stocks_pair_snapshot", table_name="product_stocks")
    op.drop_index("ix_product_stocks_warehouse_id", table_name="product_stocks")
    op.drop_table("product_stocks")
    op.drop_index("ix_warehouses_user_id", table_name="warehouses")
    op.drop_table("warehouses")
