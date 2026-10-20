"""Эквайринг из Ozon — в рублях, не в процентах: product_prices.acquiring_pct → acquiring

Ozon (`/v5/product/info/prices`, поле `acquiring`) отдаёт максимальную комиссию за эквайринг в рублях при текущей цене;
с шага 1.2 её сохраняли как процент. Значения не меняются (правило 12) — меняется только название колонки, процент
считается от цены снимка (engine.products.acquiring_pct).

Revision ID: 0030_acquiring_rub
Revises: 0029_invites
Create Date: 2026-10-20
"""

from alembic import op

revision = "0030_acquiring_rub"
down_revision = "0029_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("product_prices", "acquiring_pct", new_column_name="acquiring")


def downgrade() -> None:
    op.alter_column("product_prices", "acquiring", new_column_name="acquiring_pct")
