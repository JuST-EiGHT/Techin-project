"""Планирование: счета и остатки, статьи, операции с версиями, настройки раздела; «Откуда товар» с отсрочкой

Revision ID: 0015_planning
Revises: 0014_ozon_balance
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_planning"
down_revision = "0014_ozon_balance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("supplier_deferral_days", sa.Integer()))
    op.create_table(
        "money_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_money_accounts_user_id", "money_accounts", ["user_id"])
    op.create_table(
        "money_account_balances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_money_account_balances_account", "money_account_balances", ["account_id", "created_at"])
    op.create_table(
        "cash_categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("code", sa.String(32)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cash_categories_user_id", "cash_categories", ["user_id"])
    op.create_table(
        "money_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("op_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("cash_categories.id", ondelete="SET NULL")),
        sa.Column("counterparty", sa.String(200)),
        sa.Column("comment", sa.String(500)),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("transfer_id", sa.Uuid()),
        sa.Column("external_key", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_money_operations_user_day", "money_operations", ["user_id", "day"])
    op.create_index("ix_money_operations_op", "money_operations", ["user_id", "op_id"])
    op.create_index("ix_money_operations_external", "money_operations", ["user_id", "external_key"])
    op.create_table(
        "planning_settings",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("setup_at", sa.DateTime(timezone=True)),
        sa.Column("ozon_account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="SET NULL")),
        sa.Column("threshold", sa.Numeric(14, 2)),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("planning_settings")
    op.drop_index("ix_money_operations_external", table_name="money_operations")
    op.drop_index("ix_money_operations_op", table_name="money_operations")
    op.drop_index("ix_money_operations_user_day", table_name="money_operations")
    op.drop_table("money_operations")
    op.drop_index("ix_cash_categories_user_id", table_name="cash_categories")
    op.drop_table("cash_categories")
    op.drop_index("ix_money_account_balances_account", table_name="money_account_balances")
    op.drop_table("money_account_balances")
    op.drop_index("ix_money_accounts_user_id", table_name="money_accounts")
    op.drop_table("money_accounts")
    op.drop_column("user_settings", "supplier_deferral_days")
