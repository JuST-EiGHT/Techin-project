"""ABC-анализ (шаг 10.10): границы групп A и B, % накопленной доли

user_settings.abc_a_pct, abc_b_pct — пусто — 80 и 95 (engine/abc).

Revision ID: 0036_abc
Revises: 0035_recommendations
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_abc"
down_revision = "0035_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("abc_a_pct", sa.Integer()))
    op.add_column("user_settings", sa.Column("abc_b_pct", sa.Integer()))


def downgrade() -> None:
    op.drop_column("user_settings", "abc_b_pct")
    op.drop_column("user_settings", "abc_a_pct")
