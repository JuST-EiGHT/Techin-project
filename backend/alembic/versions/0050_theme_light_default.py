"""Светлая тема по умолчанию (решение владельца 12.11.2026, дизайн-система 10.27a)

Было: user_settings.theme по умолчанию "dark" (0008). Стало "light" — и для новых аккаунтов, и для существующих:
выбор «тёмная» не отличить от значения по умолчанию, а сервис ещё не открыт другим продавцам. Кто хочет тёмную —
одно нажатие в «Основных настройках». Тема — настройка вида, не исходные данные (DATA_HISTORY не затрагивается).

Revision ID: 0050_theme_light_default
Revises: 0049_price_extras_all
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_theme_light_default"
down_revision = "0049_price_extras_all"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("user_settings", "theme", server_default="light", existing_type=sa.String(8), existing_nullable=False)
    op.execute("UPDATE user_settings SET theme = 'light' WHERE theme = 'dark'")


def downgrade() -> None:
    # обратно — только значение по умолчанию: чей выбор был «светлая» до шага, не узнать
    op.alter_column("user_settings", "theme", server_default="dark", existing_type=sa.String(8), existing_nullable=False)
