"""Заявки покупателей на скидку (шаг 4.3a): заявки Ozon, история их статусов, решения через сервис; настройки «скоро
истекает» и «одобрили бы по правилу»

Revision ID: 20261117_discount_tasks
Revises: 20261116_supply_ozon_period
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20261117_discount_tasks"
down_revision = "20261116_supply_ozon_period"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discount_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ozon_task_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.BigInteger()),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(500)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at_ozon", sa.DateTime(timezone=True)),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("edited_till", sa.DateTime(timezone=True)),
        sa.Column("requested_price", sa.Numeric(12, 2)),
        sa.Column("requested_discount_pct", sa.Numeric(7, 2)),
        sa.Column("requested_quantity_max", sa.Integer()),
        sa.Column("original_price", sa.Numeric(12, 2)),
        sa.Column("min_auto_price", sa.Numeric(12, 2)),
        sa.Column("reduction_factor", sa.Numeric(12, 4)),
        sa.Column("approved_price", sa.Numeric(12, 2)),
        sa.Column("approved_discount", sa.Numeric(12, 2)),
        sa.Column("approved_quantity_max", sa.Integer()),
        sa.Column("moderated_at", sa.DateTime(timezone=True)),
        sa.Column("is_auto_moderated", sa.Boolean()),
        sa.Column("auto_moderated_info", sa.JSON()),
        sa.Column("raw_json", sa.JSON()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "ozon_task_id", name="uq_discount_tasks_user_task"),
    )
    op.create_index("ix_discount_tasks_user_created", "discount_tasks", ["user_id", "created_at_ozon"])
    op.create_index("ix_discount_tasks_product", "discount_tasks", ["product_id"])

    op.create_table(
        "discount_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("discount_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("preview_id", sa.Uuid(), sa.ForeignKey("discount_decisions.id")),
        sa.Column("approved_price", sa.Numeric(12, 2)),
        sa.Column("approved_quantity_max", sa.Integer()),
        sa.Column("seller_comment", sa.Text()),
        sa.Column("margin", sa.Numeric(8, 2)),
        sa.Column("unit_profit", sa.Numeric(12, 2)),
        sa.Column("breakeven_price", sa.Numeric(12, 2)),
        sa.Column("accept_loss", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("errors", sa.JSON()),
        sa.Column("warnings", sa.JSON()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_discount_decisions_user_created", "discount_decisions", ["user_id", "created_at"])
    op.create_index("ix_discount_decisions_task", "discount_decisions", ["task_id", "created_at"])
    op.create_index("ix_discount_decisions_batch", "discount_decisions", ["batch_id"])
    op.create_index("uq_discount_decisions_preview", "discount_decisions", ["preview_id"], unique=True,
                    postgresql_where=sa.text("preview_id IS NOT NULL"))

    op.create_table(
        "discount_task_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("discount_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_price", sa.Numeric(12, 2)),
        sa.Column("approved_quantity_max", sa.Integer()),
        sa.Column("moderated_at", sa.DateTime(timezone=True)),
        sa.Column("is_auto_moderated", sa.Boolean()),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("decision_id", sa.Uuid(), sa.ForeignKey("discount_decisions.id")),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discount_task_events_task", "discount_task_events", ["task_id", "seen_at"])

    op.add_column("user_settings", sa.Column("anomaly_discount_expiry_hours", sa.Integer()))
    op.add_column("user_settings", sa.Column("discount_rule_margin_pct", sa.Numeric(5, 2)))


def downgrade() -> None:
    op.drop_column("user_settings", "discount_rule_margin_pct")
    op.drop_column("user_settings", "anomaly_discount_expiry_hours")
    op.drop_index("ix_discount_task_events_task", table_name="discount_task_events")
    op.drop_table("discount_task_events")
    op.drop_index("uq_discount_decisions_preview", table_name="discount_decisions")
    op.drop_index("ix_discount_decisions_batch", table_name="discount_decisions")
    op.drop_index("ix_discount_decisions_task", table_name="discount_decisions")
    op.drop_index("ix_discount_decisions_user_created", table_name="discount_decisions")
    op.drop_table("discount_decisions")
    op.drop_index("ix_discount_tasks_product", table_name="discount_tasks")
    op.drop_index("ix_discount_tasks_user_created", table_name="discount_tasks")
    op.drop_table("discount_tasks")
