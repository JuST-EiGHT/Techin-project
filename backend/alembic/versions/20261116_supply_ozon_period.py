"""Рекомендация Ozon по поставкам — на какой срок (правка 16.11): `supply_period` Ozon — «Частота поставки» в «Настроить
рекомендации» кабинета (раз в 1 / 2 / 4 / 8 недель); настройка пользователя и срок у снимка рекомендации

Revision ID: 20261116_supply_ozon_period
Revises: 20261114_supplier_lots
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "20261116_supply_ozon_period"
down_revision = "20261114_supplier_lots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("supply_ozon_days", sa.Integer()))
    op.add_column("ozon_supply_recs", sa.Column("period_days", sa.Integer()))
    op.add_column("ozon_supply_rec_syncs", sa.Column("period_days", sa.Integer()))


def downgrade() -> None:
    op.drop_column("ozon_supply_rec_syncs", "period_days")
    op.drop_column("ozon_supply_recs", "period_days")
    op.drop_column("user_settings", "supply_ozon_days")
