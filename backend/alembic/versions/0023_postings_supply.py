"""Поставки (шаг 5.2): отправления FBO и FBS (продажи по кластерам и складам), параметры поставок пользователя

Revision ID: 0023_postings_supply
Revises: 0022_stock_settings
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_postings_supply"
down_revision = "0022_stock_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "postings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheme", sa.String(4), nullable=False),
        sa.Column("posting_number", sa.String(64), nullable=False),
        sa.Column("order_number", sa.String(64)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger()),
        sa.Column("warehouse_name", sa.String(255)),
        sa.Column("cluster_from", sa.String(255)),
        sa.Column("cluster_to", sa.String(255)),
        sa.Column("raw_json", sa.JSON()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "posting_number", name="uq_postings_user_number"),
    )
    op.create_index("ix_postings_user_scheme_day", "postings", ["user_id", "scheme", "day"])
    op.create_table(
        "posting_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("posting_id", sa.Uuid(), sa.ForeignKey("postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.BigInteger()),
        sa.Column("offer_id", sa.String(100)),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(14, 2)),
    )
    op.create_index("ix_posting_items_posting_id", "posting_items", ["posting_id"])
    op.create_index("ix_posting_items_product_id", "posting_items", ["product_id"])
    op.create_table(
        "posting_syncs",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scheme", sa.String(4), primary_key=True),
        sa.Column("loaded_from", sa.Date(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("user_settings", sa.Column("supply_target_days", sa.Integer()))
    op.add_column("user_settings", sa.Column("supply_lead_time_days", sa.Integer()))


def downgrade() -> None:
    op.drop_column("user_settings", "supply_lead_time_days")
    op.drop_column("user_settings", "supply_target_days")
    op.drop_table("posting_syncs")
    op.drop_index("ix_posting_items_product_id", table_name="posting_items")
    op.drop_index("ix_posting_items_posting_id", table_name="posting_items")
    op.drop_table("posting_items")
    op.drop_index("ix_postings_user_scheme_day", table_name="postings")
    op.drop_table("postings")
