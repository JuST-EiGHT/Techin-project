"""Операции и статьи (шаг 10.19): подстатьи и где учитывается статья

cash_categories.parent_id — статья того же вида, один уровень вложенности (факт подстатьи входит в статью);
in_pnl — расход бизнеса в P&L (шаг 10.22), in_budget — статья бюджетов (шаг 10.21). По умолчанию в P&L — расходы, кроме
закупки товара и оплаты поставщику (себестоимость уже в P&L), производства — сырьё, зарплата производства, аренда цеха,
оборудование (входят в себестоимость производства), налогов (налог уже в P&L), кредитов, вывода владельцу и переводов.

Revision ID: 0041_category_parents
Revises: 0040_supply_orders
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_category_parents"
down_revision = "0040_supply_orders"
branch_labels = None
depends_on = None

NOT_IN_PNL = ("loans", "taxes", "owner", "purchase", "supplier", "materials", "production_salary", "workshop_rent",
              "equipment", "transfer")


def upgrade() -> None:
    op.add_column("cash_categories", sa.Column("parent_id", sa.Uuid(),
                                               sa.ForeignKey("cash_categories.id", ondelete="SET NULL")))
    op.add_column("cash_categories", sa.Column("in_pnl", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("cash_categories", sa.Column("in_budget", sa.Boolean(), nullable=False, server_default=sa.true()))
    codes = ", ".join(f"'{c}'" for c in NOT_IN_PNL)
    op.execute(f"UPDATE cash_categories SET in_pnl = true WHERE kind = 'expense' AND (code IS NULL OR code NOT IN ({codes}))")
    op.execute("UPDATE cash_categories SET in_budget = false WHERE kind = 'transfer'")


def downgrade() -> None:
    op.drop_column("cash_categories", "in_budget")
    op.drop_column("cash_categories", "in_pnl")
    op.drop_column("cash_categories", "parent_id")
