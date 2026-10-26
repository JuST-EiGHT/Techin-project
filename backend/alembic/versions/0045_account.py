"""Аккаунт (шаг 10.26): имя и фамилия раздельно, телефон, удаление с отсрочкой, сеансы входа

users.first_name / last_name — из прежнего full_name («Имя Фамилия»: до первого пробела — имя, остальное — фамилия);
full_name остаётся подписью. users.phone — необязательный. users.deletion_requested_at / deletion_due_at — удаление
аккаунта с отсрочкой. user_sessions — сеансы входа (устройство, адрес, вход, последняя активность, завершён ли).
user_settings.supply_demand_days / min_profit_rub — период планирования поставок и минимальная допустимая прибыль.

Revision ID: 0045_account
Revises: 0044_notification_sections
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_account"
down_revision = "0044_notification_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(100)))
    op.add_column("users", sa.Column("last_name", sa.String(100)))
    op.add_column("users", sa.Column("phone", sa.String(32)))
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("deletion_due_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE users SET first_name = left(split_part(btrim(full_name), ' ', 1), 100), "
        "last_name = nullif(left(btrim(substr(btrim(full_name), length(split_part(btrim(full_name), ' ', 1)) + 1)), 100), '') "
        "WHERE owner_id IS NULL AND full_name IS NOT NULL AND btrim(full_name) <> ''"
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(16)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_sessions_user", "user_sessions", ["user_id", "ended_at"])
    # настройки магазина: период планирования поставок и минимальная допустимая прибыль (пусто — 30 дней и 0 ₽)
    op.add_column("user_settings", sa.Column("supply_demand_days", sa.Integer()))
    op.add_column("user_settings", sa.Column("min_profit_rub", sa.Numeric(10, 2)))


def downgrade() -> None:
    op.drop_column("user_settings", "min_profit_rub")
    op.drop_column("user_settings", "supply_demand_days")
    op.drop_index("ix_user_sessions_user", table_name="user_sessions")
    op.drop_table("user_sessions")
    for col in ("deletion_due_at", "deletion_requested_at", "phone", "last_name", "first_name"):
        op.drop_column("users", col)
