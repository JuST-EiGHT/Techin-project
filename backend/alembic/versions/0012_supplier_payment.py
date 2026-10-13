"""user_settings.supplier_payment — когда поставщик получает деньги (подпись себестоимости в P&L)

Revision ID: 0012_supplier_payment
Revises: 0011_pnl_snapshots
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_supplier_payment"
down_revision = "0011_pnl_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings", sa.Column("supplier_payment", sa.String(16), nullable=False, server_default="upfront")
    )


def downgrade() -> None:
    op.drop_column("user_settings", "supplier_payment")
