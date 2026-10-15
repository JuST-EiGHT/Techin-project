"""Плановые платежи и переопределения, генераторы плана, «оставить прошлые налоговые платежи как были»

Revision ID: 0017_planned_payments
Revises: 0016_operation_description
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_planned_payments"
down_revision = "0016_operation_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planned_payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("cash_categories.id", ondelete="SET NULL")),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="SET NULL")),
        sa.Column("counterparty", sa.String(200)),
        sa.Column("comment", sa.String(2000)),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("day", sa.Integer()),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("purchase_day", sa.Date()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_planned_payments_plan", "planned_payments", ["user_id", "plan_id"])
    op.create_table(
        "planned_overrides",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("day", sa.Date()),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        sa.Column("op_id", sa.Uuid()),
        sa.Column("paid_day", sa.Date()),
        sa.Column("paid_amount", sa.Numeric(14, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_planned_overrides_key", "planned_overrides", ["user_id", "key", "created_at"])
    op.create_table(
        "tax_plan_freezes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("due_before", sa.Date(), nullable=False),
        sa.Column("profiles_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tax_plan_freezes_user_id", "tax_plan_freezes", ["user_id"])
    op.add_column("planning_settings", sa.Column("gen_ozon", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("planning_settings", sa.Column("gen_supplier", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("planning_settings", sa.Column("gen_taxes", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("planning_settings", sa.Column("supplier_period", sa.String(8), nullable=False, server_default="week"))
    op.add_column("planning_settings", sa.Column("supplier_day", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    for column in ("supplier_day", "supplier_period", "gen_taxes", "gen_supplier", "gen_ozon"):
        op.drop_column("planning_settings", column)
    op.drop_index("ix_tax_plan_freezes_user_id", table_name="tax_plan_freezes")
    op.drop_table("tax_plan_freezes")
    op.drop_index("ix_planned_overrides_key", table_name="planned_overrides")
    op.drop_table("planned_overrides")
    op.drop_index("ix_planned_payments_plan", table_name="planned_payments")
    op.drop_table("planned_payments")
