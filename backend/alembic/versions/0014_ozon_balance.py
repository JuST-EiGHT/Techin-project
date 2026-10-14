"""ozon_balance_days, ozon_payout_settings — баланс у Ozon по дням и график выплат

Revision ID: 0014_ozon_balance
Revises: 0013_sales_daily
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_ozon_balance"
down_revision = "0013_sales_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ozon_balance_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("opening", sa.Numeric(14, 2)),
        sa.Column("accrued", sa.Numeric(14, 2)),
        sa.Column("payments", sa.Numeric(14, 2), nullable=False),
        sa.Column("closing", sa.Numeric(14, 2)),
        sa.Column("sales", sa.Numeric(14, 2)),
        sa.Column("returns", sa.Numeric(14, 2)),
        sa.Column("services", sa.Numeric(14, 2)),
        sa.Column("raw_json", sa.JSON()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ozon_balance_days_user_day", "ozon_balance_days", ["user_id", "day", "synced_at"])
    op.create_table(
        "ozon_payout_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("weekday", sa.Integer()),
        sa.Column("lag_weeks", sa.Integer()),
        sa.Column("fee_pct", sa.Numeric(5, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ozon_payout_settings_user_id", "ozon_payout_settings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ozon_payout_settings_user_id", table_name="ozon_payout_settings")
    op.drop_table("ozon_payout_settings")
    op.drop_index("ix_ozon_balance_days_user_day", table_name="ozon_balance_days")
    op.drop_table("ozon_balance_days")
