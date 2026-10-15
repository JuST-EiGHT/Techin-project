"""Банк: выписки файлом (1С), операции и остатки банка, реквизиты счёта, «это разные операции»

Revision ID: 0018_bank_statements
Revises: 0017_planned_payments
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_bank_statements"
down_revision = "0017_planned_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("money_accounts", sa.Column("number", sa.String(20)))
    op.add_column("money_accounts", sa.Column("bik", sa.String(9)))
    op.add_column("money_accounts", sa.Column("bank_name", sa.String(200)))
    op.create_table(
        "bank_statements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("version", sa.String(16)),
        sa.Column("encoding", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("date_from", sa.Date()),
        sa.Column("date_to", sa.Date()),
        sa.Column("accounts", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bank_statements_user_sha", "bank_statements", ["user_id", "sha256"])
    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("statement_id", sa.Uuid(), sa.ForeignKey("bank_statements.id", ondelete="SET NULL")),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("counterparty", sa.String(300)),
        sa.Column("counterparty_inn", sa.String(12)),
        sa.Column("counterparty_account", sa.String(20)),
        sa.Column("counterparty_bik", sa.String(9)),
        sa.Column("purpose", sa.String(2000)),
        sa.Column("doc_number", sa.String(64)),
        sa.Column("bank_id", sa.String(128)),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("op_id", sa.Uuid()),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "account_id", "dedup_key", name="uq_bank_transactions_key"),
    )
    op.create_index("ix_bank_transactions_user_day", "bank_transactions", ["user_id", "day"])
    op.create_table(
        "bank_balances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("statement_id", sa.Uuid(), sa.ForeignKey("bank_statements.id", ondelete="SET NULL")),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bank_balances_account_day", "bank_balances", ["account_id", "day"])
    op.create_table(
        "operation_merge_dismissals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bank_op_id", sa.Uuid(), nullable=False),
        sa.Column("manual_op_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_merge_dismissals_user", "operation_merge_dismissals", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_merge_dismissals_user", table_name="operation_merge_dismissals")
    op.drop_table("operation_merge_dismissals")
    op.drop_index("ix_bank_balances_account_day", table_name="bank_balances")
    op.drop_table("bank_balances")
    op.drop_index("ix_bank_transactions_user_day", table_name="bank_transactions")
    op.drop_table("bank_transactions")
    op.drop_index("ix_bank_statements_user_sha", table_name="bank_statements")
    op.drop_table("bank_statements")
    for column in ("bank_name", "bik", "number"):
        op.drop_column("money_accounts", column)
