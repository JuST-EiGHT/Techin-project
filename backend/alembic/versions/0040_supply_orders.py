"""Поставки (шаг 10.18): заявки на поставку Ozon, планы поставок, черновики заявок, страховой запас

supply_orders — заявки FBO из Ozon (последние статус и таймслот), supply_order_events — каждая смена статуса или таймслота,
supply_order_items — снимки состава заявки и актов приёмки (только добавление). supply_plans и supply_plan_items — план
поставки и его строки с версиями (ручная корректировка — новая строка, прежняя — replaced_at). supply_drafts — черновики
заявок в Ozon (предпросмотр → подтверждение). user_settings.supply_safety_days — страховой запас, дней.

Revision ID: 0040_supply_orders
Revises: 0039_withdrawal_decisions
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_supply_orders"
down_revision = "0039_withdrawal_decisions"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _user() -> sa.Column:
    return sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("supply_safety_days", sa.Integer()))
    op.create_table(
        "supply_plans",
        _id(), _user(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("destination_key", sa.String(255), nullable=False),
        sa.Column("destination_name", sa.String(255), nullable=False),
        sa.Column("planned_date", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("ozon_order_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_plans_user_created", "supply_plans", ["user_id", "created_at"])
    op.create_table(
        "supply_plan_items",
        _id(),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("supply_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recommended", sa.Integer()),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_plan_items_plan", "supply_plan_items", ["plan_id", "product_id"])
    op.create_table(
        "supply_orders",
        _id(), _user(),
        sa.Column("ozon_order_id", sa.BigInteger(), nullable=False),
        sa.Column("order_number", sa.String(64)),
        sa.Column("created_at_ozon", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("state_updated_at", sa.DateTime(timezone=True)),
        sa.Column("timeslot_from", sa.DateTime(timezone=True)),
        sa.Column("timeslot_to", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.String(64)),
        sa.Column("dropoff", sa.String(255)),
        sa.Column("storage_warehouse", sa.String(255)),
        sa.Column("cluster_name", sa.String(255)),
        sa.Column("supplies", sa.JSON()),
        sa.Column("raw_json", sa.JSON()),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("supply_plans.id", ondelete="SET NULL")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("items_synced_at", sa.DateTime(timezone=True)),
        sa.Column("acts_synced_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ozon_order_id", name="uq_supply_orders_user_order"),
    )
    op.create_index("ix_supply_orders_user_created", "supply_orders", ["user_id", "created_at_ozon"])
    op.create_table(
        "supply_order_events",
        _id(),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("supply_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("state_updated_at", sa.DateTime(timezone=True)),
        sa.Column("timeslot_from", sa.DateTime(timezone=True)),
        sa.Column("timeslot_to", sa.DateTime(timezone=True)),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_order_events_order", "supply_order_events", ["order_id", "seen_at"])
    op.create_table(
        "supply_order_items",
        _id(),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("supply_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supply_id", sa.BigInteger()),
        sa.Column("sku", sa.BigInteger()),
        sa.Column("offer_id", sa.String(100)),
        sa.Column("name", sa.String(255)),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fact", sa.Integer()),
        sa.Column("approved", sa.Integer()),
        sa.Column("defect", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_order_items_order", "supply_order_items", ["order_id", "source", "snapshot_at"])
    op.create_table(
        "supply_drafts",
        _id(), _user(),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("supply_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ozon_draft_id", sa.BigInteger()),
        sa.Column("cluster_id", sa.BigInteger()),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("items_key", sa.String(64), nullable=False),
        sa.Column("request_json", sa.JSON()),
        sa.Column("response_json", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("ozon_order_id", sa.BigInteger()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supply_drafts_plan", "supply_drafts", ["plan_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_supply_drafts_plan", table_name="supply_drafts")
    op.drop_table("supply_drafts")
    op.drop_index("ix_supply_order_items_order", table_name="supply_order_items")
    op.drop_table("supply_order_items")
    op.drop_index("ix_supply_order_events_order", table_name="supply_order_events")
    op.drop_table("supply_order_events")
    op.drop_index("ix_supply_orders_user_created", table_name="supply_orders")
    op.drop_table("supply_orders")
    op.drop_index("ix_supply_plan_items_plan", table_name="supply_plan_items")
    op.drop_table("supply_plan_items")
    op.drop_index("ix_supply_plans_user_created", table_name="supply_plans")
    op.drop_table("supply_plans")
    op.drop_column("user_settings", "supply_safety_days")
