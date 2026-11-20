"""Поставки: минимум — на всю поставку в кластер, довозить до него, если запаса хватит не больше чем на N дней
(правка 20.11, решение владельца: 6 шт., 60 дней)

Revision ID: 20261120_supply_topup_days
Revises: 20261118_merge_traffic
Create Date: 2026-11-20
"""

import sqlalchemy as sa

from alembic import op

revision = "20261120_supply_topup_days"
down_revision = "20261118_merge_traffic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("supply_topup_days", sa.Integer()))


def downgrade() -> None:
    op.drop_column("user_settings", "supply_topup_days")
