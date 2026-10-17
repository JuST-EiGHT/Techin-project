"""Аномалии (шаг 7.1): таблица anomalies (активная одна на тип и предмет — частичный уникальный индекс), пороги
аномалий и порог «низкой маржи» — настройки пользователя

Revision ID: 0027_anomalies
Revises: 0026_price_bulk
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_anomalies"
down_revision = "0026_price_bulk"
branch_labels = None
depends_on = None

INT_COLUMNS = (
    "anomaly_sales_drop_pct", "anomaly_sales_drop_red_pct", "anomaly_sales_growth_pct",
    "anomaly_price_pct", "anomaly_price_red_pct", "anomaly_payout_diff_pct",
    "anomaly_cash_gap_days", "anomaly_uncategorized_days",
)
JSON_DATA = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("low_margin_pct", sa.Numeric(5, 2)))
    for name in INT_COLUMNS:
        op.add_column("user_settings", sa.Column(name, sa.Integer()))
    op.add_column("user_settings", sa.Column("anomaly_commission_pp", sa.Numeric(4, 1)))

    op.create_table(
        "anomalies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="SET NULL")),
        sa.Column("metric_before", sa.Numeric(18, 3)),
        sa.Column("metric_after", sa.Numeric(18, 3)),
        sa.Column("threshold", sa.Numeric(12, 3)),
        sa.Column("details", JSON_DATA),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.String(16)),
        sa.Column("is_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_anomalies_user_detected", "anomalies", ["user_id", "detected_at"])
    op.create_index("ix_anomalies_product", "anomalies", ["product_id"])
    op.create_index(
        "uq_anomalies_active", "anomalies", ["user_id", "type", "key"], unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"), sqlite_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_anomalies_active", table_name="anomalies")
    op.drop_index("ix_anomalies_product", table_name="anomalies")
    op.drop_index("ix_anomalies_user_detected", table_name="anomalies")
    op.drop_table("anomalies")
    op.drop_column("user_settings", "anomaly_commission_pp")
    for name in reversed(INT_COLUMNS):
        op.drop_column("user_settings", name)
    op.drop_column("user_settings", "low_margin_pct")
