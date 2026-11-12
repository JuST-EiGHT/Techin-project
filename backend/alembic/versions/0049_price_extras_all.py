"""Расходы для всех товаров в расчёте цены (правка владельца 11.11.2026)

user_settings.price_ad_all_pct — реклама, % от цены, для всех товаров вместо начислений; price_storage_rub и
price_other_rub — хранение и прочее на 1 шт., ₽, для всех товаров. Пусто — реклама и хранение по начислениям Ozon,
прочее — 0. Своё значение у товара (product_price_extras) важнее.

Revision ID: 0049_price_extras_all
Revises: 0048_supply_ozon_clusters
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_price_extras_all"
down_revision = "0048_supply_ozon_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("price_ad_all_pct", sa.Numeric(5, 2)))
    op.add_column("user_settings", sa.Column("price_storage_rub", sa.Numeric(12, 2)))
    op.add_column("user_settings", sa.Column("price_other_rub", sa.Numeric(12, 2)))


def downgrade() -> None:
    op.drop_column("user_settings", "price_other_rub")
    op.drop_column("user_settings", "price_storage_rub")
    op.drop_column("user_settings", "price_ad_all_pct")
