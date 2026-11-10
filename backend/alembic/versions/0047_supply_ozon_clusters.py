"""Поставки по данным Ozon (шаг 10.18a): показатели товара по кластеру из остатков, рекомендованная поставка Ozon,
кластер размещения у склада, минимум на склад и склад кластера — настройки

Revision ID: 0047_supply_ozon_clusters
Revises: 0046_price_extras
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0047_supply_ozon_clusters"
down_revision = "0046_price_extras"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("warehouses", sa.Column("macrolocal_cluster_id", sa.BigInteger()))
    op.add_column("user_settings", sa.Column("supply_min_qty", sa.Integer()))
    op.add_column("user_settings", sa.Column("supply_cluster_warehouses", sa.JSON()))
    op.create_table(
        "cluster_stock_metrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("macrolocal_cluster_id", sa.BigInteger()),
        sa.Column("cluster_name", sa.String(255)),
        sa.Column("ads", sa.Numeric(12, 3)),
        sa.Column("idc", sa.Numeric(12, 1)),
        sa.Column("days_without_sales", sa.Integer()),
        sa.Column("turnover_grade", sa.String(32)),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cluster_stock_metrics_user_id", "cluster_stock_metrics", ["user_id"])
    op.create_index("ix_cluster_stock_metrics_pair", "cluster_stock_metrics", ["product_id", "cluster_id", "snapshot_at"])
    op.create_table(
        "ozon_supply_recs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("sku", sa.BigInteger(), nullable=False),
        sa.Column("macrolocal_cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_name", sa.String(255)),
        sa.Column("recommended", sa.Integer(), nullable=False),
        sa.Column("attention_level", sa.String(16)),
        sa.Column("reasons", sa.JSON()),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ozon_supply_recs_pair", "ozon_supply_recs", ["user_id", "sku", "macrolocal_cluster_id", "snapshot_at"])
    op.create_table(
        "ozon_supply_rec_syncs",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("items", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ozon_supply_rec_syncs")
    op.drop_index("ix_ozon_supply_recs_pair", table_name="ozon_supply_recs")
    op.drop_table("ozon_supply_recs")
    op.drop_index("ix_cluster_stock_metrics_pair", table_name="cluster_stock_metrics")
    op.drop_index("ix_cluster_stock_metrics_user_id", table_name="cluster_stock_metrics")
    op.drop_table("cluster_stock_metrics")
    op.drop_column("user_settings", "supply_cluster_warehouses")
    op.drop_column("user_settings", "supply_min_qty")
    op.drop_column("warehouses", "macrolocal_cluster_id")
