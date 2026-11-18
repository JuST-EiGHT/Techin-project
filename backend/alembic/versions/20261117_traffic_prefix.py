"""«Внешний трафик» (шаг 11.1): префикс для метки UTM_CAMPAIGN из кабинета Ozon — настройка магазина

Revision ID: 20261117_traffic_prefix
Revises: 20261116_supply_ozon_period
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20261117_traffic_prefix"
down_revision = "20261116_supply_ozon_period"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("traffic_utm_prefix", sa.String(40)))


def downgrade() -> None:
    op.drop_column("user_settings", "traffic_utm_prefix")
