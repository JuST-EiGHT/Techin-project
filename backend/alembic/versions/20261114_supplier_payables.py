"""Расчёты с поставщиком: записи supplier_entries (шаг 10.29, кредиторская задолженность)

Начало учёта с долгом на эту дату, закупки с отсрочкой и оплаты, внесённые вручную или файлом. Версии — как у плановых
платежей (правило 12): исправление — новая строка, удаление — deleted_at.

Revision ID: 20261114_supplier_payables
Revises: 20261112_theme_light
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20261114_supplier_payables"
down_revision = "20261112_theme_light"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("due_day", sa.Date()),
        sa.Column("number", sa.String(100)),
        sa.Column("counterparty", sa.String(200)),
        sa.Column("comment", sa.String(2000)),
        sa.Column("source", sa.String(8), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_supplier_entries_entry_id", "supplier_entries", ["entry_id"])
    op.create_index("ix_supplier_entries_user_kind", "supplier_entries", ["user_id", "kind", "day"])


def downgrade() -> None:
    op.drop_index("ix_supplier_entries_user_kind", table_name="supplier_entries")
    op.drop_index("ix_supplier_entries_entry_id", table_name="supplier_entries")
    op.drop_table("supplier_entries")
