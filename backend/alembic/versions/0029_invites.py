"""Приглашения на регистрацию (шаг 8.4): REGISTRATION_MODE=invite, код — только хэш

Revision ID: 0029_invites
Revises: 0028_notifications
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_invites"
down_revision = "0028_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("code_tail", sa.String(4), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("note", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("invites")
