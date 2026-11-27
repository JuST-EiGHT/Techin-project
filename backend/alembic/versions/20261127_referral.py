"""Реферальная программа (шаг 11.7): настройки программы магазина, участники с личными ссылками, события участников

Revision ID: 20261127_referral
Revises: 20261121_supply_ozon_demand
Create Date: 2026-11-27
"""

import sqlalchemy as sa

from alembic import op

revision = "20261127_referral"
down_revision = "20261121_supply_ozon_demand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_programs",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("campaign_name", sa.String(64), nullable=False),
        sa.Column("utm_source", sa.String(200), nullable=False),
        sa.Column("utm_medium", sa.String(200), nullable=False),
        sa.Column("store_url", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "referral_participants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("review_date", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_referral_participants_code", "referral_participants", ["user_id", "code"], unique=True)
    op.create_index("ix_referral_participants_user", "referral_participants", ["user_id", "created_at"])
    op.create_table(
        "referral_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_id", sa.Uuid(), sa.ForeignKey("referral_participants.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16)),
        sa.Column("sent_on", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_referral_events_participant", "referral_events", ["user_id", "participant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("referral_events")
    op.drop_table("referral_participants")
    op.drop_table("referral_programs")
