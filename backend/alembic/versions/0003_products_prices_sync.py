"""products, product_prices, sync_runs

Revision ID: 0003_products_prices_sync
Revises: 0002_create_api_credentials
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_products_prices_sync"
down_revision = "0002_create_api_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ozon_product_id", sa.BigInteger(), nullable=False),
        sa.Column("ozon_sku", sa.BigInteger(), nullable=True),
        sa.Column("offer_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("barcode", sa.String(100), nullable=True),
        sa.Column("vat", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "ozon_product_id", name="uq_products_user_product"),
    )
    op.create_index("ix_products_user_id", "products", ["user_id"])

    op.create_table(
        "product_prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("old_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("marketing_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("commission_pct", sa.Numeric(7, 3), nullable=True),
        sa.Column("acquiring_pct", sa.Numeric(12, 3), nullable=True),
        sa.Column("auto_action_enabled", sa.Boolean(), nullable=True),
        sa.Column("price_index", sa.String(32), nullable=True),
    )
    op.create_index("ix_product_prices_product_snapshot", "product_prices", ["product_id", "snapshot_at"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_sync_runs_user_kind_started", "sync_runs", ["user_id", "kind", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_user_kind_started", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index("ix_product_prices_product_snapshot", table_name="product_prices")
    op.drop_table("product_prices")
    op.drop_index("ix_products_user_id", table_name="products")
    op.drop_table("products")
