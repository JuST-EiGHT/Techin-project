"""Доп. расходы в расчёте цены (правка владельца 06.11.2026)

user_settings.price_extra_pct — доп. расходы магазина, % от цены, для всех товаров (пусто — 0).
product_price_extras — свои расходы товара: хранение и прочее на 1 шт., ₽, и доп. расходы, % (пусто — как у магазина).

Revision ID: 0046_price_extras
Revises: 0045_account
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_price_extras"
down_revision = "0045_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("price_extra_pct", sa.Numeric(5, 2)))
    op.create_table(
        "product_price_extras",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage", sa.Numeric(12, 2)),
        sa.Column("other", sa.Numeric(12, 2)),
        sa.Column("extra_pct", sa.Numeric(5, 2)),
        sa.Column("note", sa.String(200)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "product_id", name="uq_product_price_extras"),
    )


def downgrade() -> None:
    op.drop_table("product_price_extras")
    op.drop_column("user_settings", "price_extra_pct")
