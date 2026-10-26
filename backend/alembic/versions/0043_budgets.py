"""Бюджеты и сценарии (шаг 10.21): план по статьям на месяц с версиями, множители сценариев

budget_lines — план статьи на месяц (квартал и год — суммы месяцев); изменение — новая строка, прежняя — superseded_at.
planning_settings.scen_* — на сколько % плановые поступления и платежи оптимистичного и пессимистичного сценария больше
или меньше базового.

Revision ID: 0043_budgets
Revises: 0042_rule_runs
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_budgets"
down_revision = "0042_rule_runs"
branch_labels = None
depends_on = None

SCENARIOS = (("scen_opt_income", "10"), ("scen_opt_expense", "-5"), ("scen_pes_income", "-10"), ("scen_pes_expense", "10"))


def upgrade() -> None:
    op.create_table(
        "budget_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("cash_categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("source", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_budget_lines_user_month", "budget_lines", ["user_id", "month"])
    for name, default in SCENARIOS:
        op.add_column("planning_settings", sa.Column(name, sa.Numeric(5, 1), nullable=False, server_default=default))


def downgrade() -> None:
    for name, _ in SCENARIOS:
        op.drop_column("planning_settings", name)
    op.drop_index("ix_budget_lines_user_month", table_name="budget_lines")
    op.drop_table("budget_lines")
