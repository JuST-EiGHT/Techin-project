"""money_operations.comment — описание операции до 2000 знаков (полностью — в карточке операции)

Revision ID: 0016_operation_description
Revises: 0015_planning
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_operation_description"
down_revision = "0015_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("money_operations", "comment", type_=sa.String(2000), existing_type=sa.String(500))


def downgrade() -> None:
    op.alter_column("money_operations", "comment", type_=sa.String(500), existing_type=sa.String(2000))
