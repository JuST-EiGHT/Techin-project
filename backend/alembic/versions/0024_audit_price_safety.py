"""Безопасность изменения цен (шаг 6.1): audit_log расширен (JSONB, товар, запрос к API), записи нельзя менять и
удалять (триггер); таблица изменений цен price_changes

Revision ID: 0024_audit_price_safety
Revises: 0023_postings_supply
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_audit_price_safety"
down_revision = "0023_postings_supply"
branch_labels = None
depends_on = None

JSON_COLUMNS = ("before_json", "after_json", "ozon_request", "ozon_response")

# Журнал только пополняется. Удалить записи можно только намеренно (например, при удалении аккаунта — когда оно
# появится): SET LOCAL app.audit_log_allow_delete = 'on' в той же транзакции. Менять — никогда.
IMMUTABLE_FUNCTION = """
CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND coalesce(current_setting('app.audit_log_allow_delete', true), '') = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_log: записи журнала не меняются и не удаляются';
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    for col in JSON_COLUMNS:
        op.alter_column(
            "audit_log", col, type_=postgresql.JSONB(), existing_type=sa.JSON(), postgresql_using=f"{col}::jsonb"
        )
    op.add_column("audit_log", sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE")))
    op.add_column("audit_log", sa.Column("request_id", sa.Uuid()))
    op.add_column("audit_log", sa.Column("method", sa.String(10)))
    op.add_column("audit_log", sa.Column("path", sa.String(500)))
    op.add_column("audit_log", sa.Column("http_status", sa.Integer()))
    # записи шага 4.3 — товар по «<акция Ozon>:<товар Ozon>» (до триггера: после него строки не меняются)
    op.execute(
        "UPDATE audit_log a SET product_id = p.id FROM products p "
        "WHERE a.entity_type = 'promotion_product' AND p.user_id = a.user_id "
        "AND split_part(a.entity_id, ':', 2) ~ '^[0-9]+$' "
        "AND p.ozon_product_id = split_part(a.entity_id, ':', 2)::bigint"
    )
    op.create_index("ix_audit_log_user_product", "audit_log", ["user_id", "product_id", "created_at"])
    op.create_index("ix_audit_log_request", "audit_log", ["request_id"])
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()"
    )

    op.create_table(
        "price_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("price_before", sa.Numeric(12, 2)),
        sa.Column("price_after", sa.Numeric(12, 2)),
        sa.Column("min_price_before", sa.Numeric(12, 2)),
        sa.Column("min_price_after", sa.Numeric(12, 2)),
        sa.Column("is_dry_run", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_applied", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("errors", sa.JSON()),
        sa.Column("warnings", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("rollback_of_id", sa.Uuid(), sa.ForeignKey("price_changes.id")),
    )
    op.create_index("ix_price_changes_user_created", "price_changes", ["user_id", "created_at"])
    op.create_index("ix_price_changes_product_created", "price_changes", ["product_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_price_changes_product_created", table_name="price_changes")
    op.drop_index("ix_price_changes_user_created", table_name="price_changes")
    op.drop_table("price_changes")

    # записи запросов к API (middleware) в журнал шага 4.3 не помещаются по смыслу — при откате удаляются
    op.execute("DROP TRIGGER audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION audit_log_immutable()")
    op.execute("DELETE FROM audit_log WHERE entity_type = 'http'")
    op.drop_index("ix_audit_log_request", table_name="audit_log")
    op.drop_index("ix_audit_log_user_product", table_name="audit_log")
    for col in ("http_status", "path", "method", "request_id", "product_id"):
        op.drop_column("audit_log", col)
    for col in JSON_COLUMNS:
        op.alter_column(
            "audit_log", col, type_=sa.JSON(), existing_type=postgresql.JSONB(), postgresql_using=f"{col}::json"
        )
