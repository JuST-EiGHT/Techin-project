"""Юнит-экономика как у Ozon (шаг 4.2a)

storage_costs — строки отчёта Ozon «Стоимость размещения по товарам» (файл — в ozon_documents): по ним хранение магазина
за день делится между товарами.
product_price_extras.ad_pct — реклама, % от цены, своя у товара (пусто — по факту начислений).

Revision ID: 0047_unit_economics
Revises: 0046_price_extras
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0047_unit_economics"
down_revision = "0046_price_extras"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_price_extras", sa.Column("ad_pct", sa.Numeric(5, 2)))
    op.create_table(
        "storage_costs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("ozon_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date()),
        sa.Column("sku", sa.BigInteger()),
        sa.Column("offer_id", sa.String(100)),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_storage_costs_user_day", "storage_costs", ["user_id", "day"])
    op.create_index("ix_storage_costs_document_id", "storage_costs", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_storage_costs_document_id", table_name="storage_costs")
    op.drop_index("ix_storage_costs_user_day", table_name="storage_costs")
    op.drop_table("storage_costs")
    op.drop_column("product_price_extras", "ad_pct")
