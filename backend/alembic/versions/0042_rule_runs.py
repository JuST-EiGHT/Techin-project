"""Правила и счета (шаг 10.20): условия «счёт» и «источник» у правил, журнал применения правил, отклонённые
предложения план-факта

categorization_rules.account_id / source — новые условия правила (пусто — любой счёт и источник). rule_runs — журнал
применения правил: когда, почему, сколько операций проверено и изменено, какие, ошибки и предупреждения (только
добавление). plan_match_dismissals — «это не оплата этого платежа» (не удаляются).

Revision ID: 0042_rule_runs
Revises: 0041_category_parents
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_rule_runs"
down_revision = "0041_category_parents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("categorization_rules", sa.Column("account_id", sa.Uuid(),
                                                    sa.ForeignKey("money_accounts.id", ondelete="SET NULL")))
    op.add_column("categorization_rules", sa.Column("source", sa.String(8)))
    op.create_table(
        "rule_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("rule_id", sa.Uuid()),
        sa.Column("checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("problems", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_runs_user", "rule_runs", ["user_id", "created_at"])
    op.create_table(
        "plan_match_dismissals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("op_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plan_match_dismissals_user", "plan_match_dismissals", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_match_dismissals_user", table_name="plan_match_dismissals")
    op.drop_table("plan_match_dismissals")
    op.drop_index("ix_rule_runs_user", table_name="rule_runs")
    op.drop_table("rule_runs")
    op.drop_column("categorization_rules", "source")
    op.drop_column("categorization_rules", "account_id")
