"""Склады (шаг 5.1): пороги статуса остатка и параметры отчёта «Кандидаты на вывоз» — настройки пользователя

Revision ID: 0022_stock_settings
Revises: 0021_promotion_history
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_stock_settings"
down_revision = "0021_promotion_history"
branch_labels = None
depends_on = None

COLUMNS = (
    "stock_critical_days", "stock_low_days", "stock_excess_days",
    "withdrawal_total", "withdrawal_keep_cluster", "withdrawal_keep_selling",
)


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("user_settings", sa.Column(name, sa.Integer()))
    op.add_column("user_settings", sa.Column("withdrawal_excluded_clusters", sa.JSON()))


def downgrade() -> None:
    op.drop_column("user_settings", "withdrawal_excluded_clusters")
    for name in reversed(COLUMNS):
        op.drop_column("user_settings", name)
