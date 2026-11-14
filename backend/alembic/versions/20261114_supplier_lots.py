"""Долги поставщикам (шаг 10.23a): документы, поставки со строками и оплаты поставщику

Поставка — версии (lot_id), у версии свои строки; документ хранится как есть; оплаты — версии (payment_id). Правило 12:
ничего не перезаписывается и не удаляется — исправление новой версией, удаление — deleted_at.

Revision ID: 20261114_supplier_lots
Revises: 20261112_theme_light
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20261114_supplier_lots"
down_revision = "20261112_theme_light"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_supplier_documents_user_id", "supplier_documents", ["user_id"])
    op.create_table(
        "supplier_lots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("supplier", sa.String(300)),
        sa.Column("receiver", sa.String(300)),
        sa.Column("warehouse", sa.String(200)),
        sa.Column("doc_total_qty", sa.Integer()),
        sa.Column("source", sa.String(8), nullable=False, server_default="pdf"),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("supplier_documents.id", ondelete="SET NULL")),
        sa.Column("comment", sa.String(2000)),
        sa.Column("closed_on", sa.Date()),
        sa.Column("close_reason", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_supplier_lots_user_lot", "supplier_lots", ["user_id", "lot_id"])
    op.create_table(
        "supplier_lot_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lot_row_id", sa.Uuid(), sa.ForeignKey("supplier_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("article", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300)),
        sa.Column("barcode", sa.String(64)),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("sold_before", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("written_off", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_supplier_lot_lines_lot_row_id", "supplier_lot_lines", ["lot_row_id"])
    op.create_table(
        "supplier_payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("counterparty", sa.String(200)),
        sa.Column("comment", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_supplier_payments_payment_id", "supplier_payments", ["payment_id"])
    op.create_index("ix_supplier_payments_user_day", "supplier_payments", ["user_id", "day"])


def downgrade() -> None:
    op.drop_table("supplier_payments")
    op.drop_table("supplier_lot_lines")
    op.drop_table("supplier_lots")
    op.drop_table("supplier_documents")
