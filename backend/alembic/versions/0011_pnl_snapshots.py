"""pnl_snapshots — P&L закрытых месяцев для быстрого дашборда

Revision ID: 0011_pnl_snapshots
Revises: 0010_tax_profiles
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_pnl_snapshots"
down_revision = "0010_tax_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pnl_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stale_reason", sa.String(255)),
        sa.Column("stale_since", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "date_from", "date_to", "source", name="uq_pnl_snapshots_period"),
    )
    op.create_index("ix_pnl_snapshots_user_id", "pnl_snapshots", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_pnl_snapshots_user_id", table_name="pnl_snapshots")
    op.drop_table("pnl_snapshots")
