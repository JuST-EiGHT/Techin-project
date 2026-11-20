"""Поставки: спрос покупателей кластера из «Локальности продаж» (правка 21.11) — сколько купили покупатели кластера за
период и сколько из них отправлено с его складов; по нему считаем рекомендацию Ozon по формуле Ozon, если продаж
кластера в «Управлении остатками» нет

Revision ID: 20261121_supply_ozon_demand
Revises: 20261120_supply_topup_days
Create Date: 2026-11-21
"""

import sqlalchemy as sa

from alembic import op

revision = "20261121_supply_ozon_demand"
down_revision = "20261120_supply_topup_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ozon_supply_recs", sa.Column("demand_qty", sa.Integer()))
    op.add_column("ozon_supply_recs", sa.Column("local_qty", sa.Integer()))


def downgrade() -> None:
    op.drop_column("ozon_supply_recs", "local_qty")
    op.drop_column("ozon_supply_recs", "demand_qty")
