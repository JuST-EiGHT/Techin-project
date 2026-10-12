"""create api_credentials table

Revision ID: 0002_create_api_credentials
Revises: 0001_create_users
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_create_api_credentials"
down_revision = "0001_create_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("client_id", sa.String(32), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_api_credentials_user_id", "api_credentials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_credentials_user_id", table_name="api_credentials")
    op.drop_table("api_credentials")
