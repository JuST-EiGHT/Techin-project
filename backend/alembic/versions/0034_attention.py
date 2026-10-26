"""«Требует внимания» (шаг 10.5): деньги под риском у аномалий, пороги проверок данных, категория «Данные» в Telegram

anomalies.money_at_risk — сколько денег под риском, ₽ (убыток в акции, недополученная выплата, стоимость лишнего
запаса), где это можно оценить; обновляется каждой проверкой, пока аномалия активна. user_settings — пороги новых
проверок: данные не обновлялись дольше N часов, дней без начислений между загруженными. notification_settings.notify_data
— присылать ли в Telegram проблемы с данными (по умолчанию нет).

Revision ID: 0034_attention
Revises: 0033_ozon_categories
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_attention"
down_revision = "0033_ozon_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anomalies", sa.Column("money_at_risk", sa.Numeric(14, 2)))
    op.add_column("user_settings", sa.Column("anomaly_sync_stale_hours", sa.Integer()))
    op.add_column("user_settings", sa.Column("anomaly_data_gap_days", sa.Integer()))
    op.add_column("notification_settings",
                  sa.Column("notify_data", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("notification_settings", "notify_data")
    op.drop_column("user_settings", "anomaly_data_gap_days")
    op.drop_column("user_settings", "anomaly_sync_stale_hours")
    op.drop_column("anomalies", "money_at_risk")
