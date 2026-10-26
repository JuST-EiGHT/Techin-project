"""Склады (шаг 10.17): решения «вывозим» / «оставить» в «На вывоз»

withdrawal_decisions — решение пользователя по товару с датой; каждое нажатие — новая запись, действует последняя,
«Отменить решение» — запись undecided. Записи не удаляются (правило 12).

Revision ID: 0039_withdrawal_decisions
Revises: 0038_offer_aliases
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_withdrawal_decisions"
down_revision = "0038_offer_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "withdrawal_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("qty", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_withdrawal_decisions_user_product", "withdrawal_decisions", ["user_id", "product_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_withdrawal_decisions_user_product", table_name="withdrawal_decisions")
    op.drop_table("withdrawal_decisions")
