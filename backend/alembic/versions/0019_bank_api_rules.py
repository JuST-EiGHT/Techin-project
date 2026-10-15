"""Банки по API и правила разнесения: подключения банков, правила, кто поставил статью операции

Revision ID: 0019_bank_api_rules
Revises: 0018_bank_statements
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_bank_api_rules"
down_revision = "0018_bank_statements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bank_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="active"),
        sa.Column("status_message", sa.String(500)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("accounts", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bank_connections_user", "bank_connections", ["user_id"])
    op.add_column("bank_transactions", sa.Column(
        "connection_id", sa.Uuid(), sa.ForeignKey("bank_connections.id", ondelete="SET NULL")))

    op.create_table(
        "categorization_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("counterparty_inn", sa.String(12)),
        sa.Column("counterparty", sa.String(200)),
        sa.Column("purpose", sa.String(500)),
        sa.Column("amount_min", sa.Numeric(14, 2)),
        sa.Column("amount_max", sa.Numeric(14, 2)),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("cash_categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_categorization_rules_user", "categorization_rules", ["user_id", "priority"])

    op.add_column("money_operations", sa.Column("category_by", sa.String(8)))
    op.add_column("money_operations", sa.Column("rule_id", sa.Uuid()))
    # кто поставил статью у уже внесённых операций: вручную и файлом — пользователь; выплаты Ozon — сервис; из банка
    # (шаг 3.11) сервис ставил только «Поступления от маркетплейсов» и «Перевод», другие статьи — от пользователя
    op.execute("UPDATE money_operations SET category_by = 'user' "
               "WHERE category_id IS NOT NULL AND source IN ('manual', 'import')")
    op.execute("UPDATE money_operations SET category_by = 'auto' WHERE category_id IS NOT NULL AND source = 'ozon_payout'")
    op.execute(
        "UPDATE money_operations SET category_by = CASE WHEN category_id IN "
        "(SELECT id FROM cash_categories WHERE code IN ('marketplace', 'transfer')) THEN 'auto' ELSE 'user' END "
        "WHERE category_id IS NOT NULL AND source = 'bank'"
    )


def downgrade() -> None:
    op.drop_column("money_operations", "rule_id")
    op.drop_column("money_operations", "category_by")
    op.drop_index("ix_categorization_rules_user", table_name="categorization_rules")
    op.drop_table("categorization_rules")
    op.drop_column("bank_transactions", "connection_id")
    op.drop_index("ix_bank_connections_user", table_name="bank_connections")
    op.drop_table("bank_connections")
