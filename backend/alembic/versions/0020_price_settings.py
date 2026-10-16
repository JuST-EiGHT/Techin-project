"""Калькулятор цены (шаг 4.2): реклама, % от цены, и целевая маржа — настройки пользователя

Revision ID: 0020_price_settings
Revises: 0019_bank_api_rules
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_price_settings"
down_revision = "0019_bank_api_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("price_ad_pct", sa.Numeric(5, 2)))
    op.add_column("user_settings", sa.Column("price_target_margin_pct", sa.Numeric(5, 2)))


def downgrade() -> None:
    op.drop_column("user_settings", "price_target_margin_pct")
    op.drop_column("user_settings", "price_ad_pct")
