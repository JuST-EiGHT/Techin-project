"""Отчёты Ozon и документы (шаг 10.12): источник отчёта и документы, полученные через API

reports.source — file (загружен XLSX) | api (получен через Ozon API; исходный ответ — в report_files, JSON).
ozon_documents — документы, которые Ozon формирует по запросу и отдаёт файлом (взаиморасчёты, компенсации,
декомпенсации): запрос, статус, файл. Записи не удаляются (правило 12): повторный запрос — новая запись.

Revision ID: 0037_ozon_documents
Revises: 0036_abc
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_ozon_documents"
down_revision = "0036_abc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("source", sa.String(8), nullable=False, server_default="file"))
    op.create_table(
        "ozon_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ozon_code", sa.String(255)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("file_name", sa.String(255)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("content", sa.LargeBinary()),
        sa.Column("info_json", sa.JSON()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ozon_documents_user_requested", "ozon_documents", ["user_id", "requested_at"])


def downgrade() -> None:
    op.drop_index("ix_ozon_documents_user_requested", table_name="ozon_documents")
    op.drop_table("ozon_documents")
    op.drop_column("reports", "source")
