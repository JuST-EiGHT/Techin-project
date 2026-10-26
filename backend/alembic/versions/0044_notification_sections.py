"""Уведомления по разделам (шаг 10.25): настройки разделов и время сводки раз в день

notification_settings.sections — по разделам карты: включён ли, Telegram, важность, сразу или сводкой; пусто — разделы
читаются из прежних категорий notify_* (шаг 7.3), поэтому выбор пользователей не теряется и данные не переносим.
notification_settings.digest_at — во сколько (минуты от полуночи, пояс пользователя) уходит сводка раз в день.

Revision ID: 0044_notification_sections
Revises: 0043_budgets
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_notification_sections"
down_revision = "0043_budgets"
branch_labels = None
depends_on = None

JSON_DATA = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("notification_settings", sa.Column("sections", JSON_DATA))
    op.add_column("notification_settings", sa.Column("digest_at", sa.Integer(), nullable=False, server_default=str(9 * 60)))


def downgrade() -> None:
    op.drop_column("notification_settings", "digest_at")
    op.drop_column("notification_settings", "sections")
