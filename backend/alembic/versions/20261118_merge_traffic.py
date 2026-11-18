"""слияние веток: заявки на скидку и внешний трафик

Revision ID: 20261118_merge_traffic
Revises: 20261117_discount_tasks, 20261118_traffic_links
Create Date: 2026-11-18 16:04:36.272986
"""
from alembic import op
import sqlalchemy as sa


revision = '20261118_merge_traffic'
down_revision = ('20261117_discount_tasks', '20261118_traffic_links')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
