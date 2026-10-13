"""reports, report_files; accruals: строки из XLSX

Revision ID: 0007_reports
Revises: 0006_promotions
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_reports"
down_revision = "0006_promotions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("report_type", sa.String(16), nullable=False),
        sa.Column("period", sa.String(100), nullable=False, server_default=""),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("manual_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("vat_multiplier", sa.Numeric(5, 3), nullable=False, server_default="1.22"),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0.20"),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), sa.ForeignKey("reports.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_reports_user_type_period", "reports", ["user_id", "report_type", "period_start"])
    op.create_index("ix_reports_user_sha256", "reports", ["user_id", "file_sha256"])

    op.create_table(
        "report_files",
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )

    op.alter_column("accruals", "accrual_type_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("accruals", sa.Column("type_name", sa.String(255), nullable=True))
    op.add_column("accruals", sa.Column("offer_id", sa.String(100), nullable=True))
    op.add_column(
        "accruals",
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_accruals_report_id", "accruals", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_accruals_report_id", table_name="accruals")
    op.execute("DELETE FROM accruals WHERE source = 'xlsx'")
    op.drop_column("accruals", "report_id")
    op.drop_column("accruals", "offer_id")
    op.drop_column("accruals", "type_name")
    op.alter_column("accruals", "accrual_type_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("report_files")
    op.drop_index("ix_reports_user_sha256", table_name="reports")
    op.drop_index("ix_reports_user_type_period", table_name="reports")
    op.drop_table("reports")
