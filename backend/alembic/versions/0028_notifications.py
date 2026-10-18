"""Уведомления в Telegram (шаг 7.3): настройки уведомлений пользователя и журнал отправок

Revision ID: 0028_notifications
Revises: 0027_anomalies
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_notifications"
down_revision = "0027_anomalies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("telegram_username", sa.String(64)),
        sa.Column("telegram_name", sa.String(200)),
        sa.Column("linked_at", sa.DateTime(timezone=True)),
        sa.Column("blocked_at", sa.DateTime(timezone=True)),
        sa.Column("link_code_hash", sa.String(64)),
        sa.Column("link_code_expires_at", sa.DateTime(timezone=True)),
        sa.Column("notify_red", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_yellow", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_auto_promo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_money", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quiet_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quiet_from", sa.Integer(), nullable=False, server_default=str(23 * 60)),
        sa.Column("quiet_to", sa.Integer(), nullable=False, server_default=str(8 * 60)),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notification_settings_chat", "notification_settings", ["chat_id"])
    op.create_index("ix_notification_settings_code", "notification_settings", ["link_code_hash"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("anomaly_id", sa.Uuid(), sa.ForeignKey("anomalies.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_notification_log_due", "notification_log", ["status", "send_after"])
    op.create_index("ix_notification_log_user_created", "notification_log", ["user_id", "created_at"])
    op.create_index(
        "uq_notification_log_anomaly", "notification_log", ["anomaly_id"], unique=True,
        postgresql_where=sa.text("anomaly_id IS NOT NULL"), sqlite_where=sa.text("anomaly_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notification_log_anomaly", table_name="notification_log")
    op.drop_index("ix_notification_log_user_created", table_name="notification_log")
    op.drop_index("ix_notification_log_due", table_name="notification_log")
    op.drop_table("notification_log")
    op.drop_index("ix_notification_settings_code", table_name="notification_settings")
    op.drop_index("ix_notification_settings_chat", table_name="notification_settings")
    op.drop_table("notification_settings")
