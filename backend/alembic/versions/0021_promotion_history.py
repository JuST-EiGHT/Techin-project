"""Акции (шаг 4.3): история участия (дата выхода, снимки цены и количества), минимум штук у кандидата, журнал
действий audit_log

Revision ID: 0021_promotion_history
Revises: 0020_price_settings
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_promotion_history"
down_revision = "0020_price_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("promotion_products", sa.Column("left_at", sa.DateTime(timezone=True)))
    op.add_column("promotion_products", sa.Column("min_stock", sa.Integer()))
    op.add_column("promotion_products", sa.Column("recommended_stock", sa.Integer()))
    # пара товар × акция теперь повторяется: прошлые участия остаются, текущая строка (left_at IS NULL) — одна
    op.drop_constraint("uq_promotion_products_pair", "promotion_products", type_="unique")
    op.create_index(
        "uq_promotion_products_current", "promotion_products", ["promotion_id", "product_id"], unique=True,
        postgresql_where=sa.text("left_at IS NULL"), sqlite_where=sa.text("left_at IS NULL"),
    )
    op.create_table(
        "promotion_price_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "promotion_product_id", sa.Uuid(), sa.ForeignKey("promotion_products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_price", sa.Numeric(12, 2)),
        sa.Column("stock", sa.Integer()),
    )
    op.create_index(
        "ix_promotion_price_snapshots_row", "promotion_price_snapshots", ["promotion_product_id", "snapshot_at"]
    )
    # уже участвующие товары: цена на момент входа неизвестна — первый снимок с тем, что есть сейчас
    op.execute(
        "INSERT INTO promotion_price_snapshots (id, promotion_product_id, snapshot_at, action_price, stock) "
        "SELECT gen_random_uuid(), id, first_seen_at, action_price, stock FROM promotion_products WHERE is_active"
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("before_json", sa.JSON()),
        sa.Column("after_json", sa.JSON()),
        sa.Column("ozon_request", sa.JSON()),
        sa.Column("ozon_response", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
    )
    op.create_index("ix_audit_log_user_entity", "audit_log", ["user_id", "entity_type", "entity_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_user_entity", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_promotion_price_snapshots_row", table_name="promotion_price_snapshots")
    op.drop_table("promotion_price_snapshots")
    # прошлые участия не помещаются в старую уникальность — при откате они удаляются (история теряется)
    op.execute("DELETE FROM promotion_products WHERE left_at IS NOT NULL")
    op.drop_index("uq_promotion_products_current", table_name="promotion_products")
    op.create_unique_constraint("uq_promotion_products_pair", "promotion_products", ["promotion_id", "product_id"])
    op.drop_column("promotion_products", "recommended_stock")
    op.drop_column("promotion_products", "min_stock")
    op.drop_column("promotion_products", "left_at")
