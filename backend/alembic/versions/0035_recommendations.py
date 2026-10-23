"""«Рекомендуемые действия» (шаг 10.6): скрытые рекомендации и срок скрытия

recommendation_hides — «Скрыть» на N дней: каждое нажатие — новая запись, «Вернуть скрытые» — cancelled_at.
user_settings.recommendation_hide_days — на сколько дней скрывать (пусто — 7).

Revision ID: 0035_recommendations
Revises: 0034_attention
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_recommendations"
down_revision = "0034_attention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_hides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("hidden_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recommendation_hides_user_until", "recommendation_hides", ["user_id", "hidden_until"])
    op.add_column("user_settings", sa.Column("recommendation_hide_days", sa.Integer()))


def downgrade() -> None:
    op.drop_column("user_settings", "recommendation_hide_days")
    op.drop_index("ix_recommendation_hides_user_until", table_name="recommendation_hides")
    op.drop_table("recommendation_hides")
