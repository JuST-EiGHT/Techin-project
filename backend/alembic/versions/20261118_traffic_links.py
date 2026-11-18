"""«Внешний трафик» (шаг 11.2): история ссылок с UTM-метками — версии, диплинк и подменный артикул из кабинета Ozon;
отметки «открыл ссылку с диплинком»

Revision ID: 20261118_traffic_links
Revises: 20261117_traffic_prefix
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "20261118_traffic_links"
down_revision = "20261117_traffic_prefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traffic_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("section", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("campaign", sa.String(100), nullable=False),
        sa.Column("campaign_name", sa.String(64), nullable=False),
        sa.Column("utm_source", sa.String(200), nullable=False),
        sa.Column("utm_medium", sa.String(200), nullable=False),
        sa.Column("utm_term", sa.String(200), nullable=False, server_default=""),
        sa.Column("utm_content", sa.String(200), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("deeplink", sa.Text()),
        sa.Column("deeplink_since", sa.DateTime(timezone=True)),
        sa.Column("substitute_code", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_traffic_links_user_current", "traffic_links", ["user_id", "superseded_at"])
    op.create_index("ix_traffic_links_link", "traffic_links", ["link_id", "version"], unique=True)
    op.create_table(
        "traffic_link_opens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_traffic_link_opens_link", "traffic_link_opens", ["user_id", "link_id", "opened_at"])


def downgrade() -> None:
    op.drop_table("traffic_link_opens")
    op.drop_table("traffic_links")
