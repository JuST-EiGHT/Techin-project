"""user_settings; users.tokens_valid_after; reports.tax_base

Revision ID: 0008_user_settings
Revises: 0007_reports
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_user_settings"
down_revision = "0007_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("theme", sa.String(8), nullable=False, server_default="dark"),
        sa.Column("vat_multiplier", sa.Numeric(5, 3), nullable=False, server_default="1.22"),
        sa.Column("tax_base", sa.String(8), nullable=False, server_default="profit"),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0.20"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("users", sa.Column("tokens_valid_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reports", sa.Column("tax_base", sa.String(8), nullable=False, server_default="profit"))


def downgrade() -> None:
    op.drop_column("reports", "tax_base")
    op.drop_column("users", "tokens_valid_after")
    op.drop_table("user_settings")
