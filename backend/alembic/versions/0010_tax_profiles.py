"""tax_profiles; налог и НДС — из налогового профиля, а не из «Основных настроек»

Свои настройки налога (user_settings: vat_multiplier, tax_base, tax_rate) переносятся в профиль
«Свой процент» с начала учёта — расчёт не меняется. Без настроек профиль не создаётся: налог
«не задан», пока пользователь не выберет систему налогообложения (правило 13).
У отчётов поля налога становятся необязательными: пусто — по профилю на дату периода. Значения,
которые при загрузке скопировались из настроек, очищаются; отличающиеся (ручные правки) остаются.

Revision ID: 0010_tax_profiles
Revises: 0009_product_costs
Create Date: 2026-10-13
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0010_tax_profiles"
down_revision = "0009_product_costs"
branch_labels = None
depends_on = None

BEGINNING = date(2000, 1, 1)
OLD_DEFAULTS = {"vat_multiplier": "1.22", "tax_base": "'profit'", "tax_rate": "0.20"}


def upgrade() -> None:
    op.create_table(
        "tax_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("legal_form", sa.String(16)),
        sa.Column("regime", sa.String(24), nullable=False),
        sa.Column("rate_pct", sa.Numeric(5, 2)),
        sa.Column("custom_base", sa.String(8)),
        sa.Column("vat_mode", sa.String(8), nullable=False, server_default="none"),
        sa.Column("vat_rate_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("supplier_vat_pct", sa.Numeric(5, 2), nullable=False, server_default="22"),
        sa.Column("contributions_year", sa.Numeric(12, 2)),
        sa.Column("has_employees", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("patent_cost_year", sa.Numeric(12, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tax_profiles_user_from", "tax_profiles", ["user_id", "effective_from"])

    conn = op.get_bind()
    profiles = sa.table(
        "tax_profiles",
        *(
            sa.column(c)
            for c in (
                "id",
                "user_id",
                "effective_from",
                "regime",
                "rate_pct",
                "custom_base",
                "vat_mode",
                "vat_rate_pct",
                "supplier_vat_pct",
                "has_employees",
                "created_at",
            )
        ),
    )
    now = datetime.now(timezone.utc)
    rows = conn.execute(sa.text("SELECT user_id, vat_multiplier, tax_base, tax_rate FROM user_settings")).fetchall()
    for user_id, vat, base, rate in rows:
        conn.execute(
            profiles.insert().values(
                id=uuid.uuid4(),
                user_id=user_id,
                effective_from=BEGINNING,
                regime="custom",
                rate_pct=(Decimal(rate) * 100).quantize(Decimal("0.01")),
                custom_base=base,
                vat_mode="none",
                vat_rate_pct=0,
                supplier_vat_pct=((Decimal(vat) - 1) * 100).quantize(Decimal("0.01")),
                has_employees=False,
                created_at=now,
            )
        )

    for column in ("vat_multiplier", "tax_base", "tax_rate"):
        op.alter_column("reports", column, nullable=True, server_default=None)
        # значение, скопированное из настроек (или прежнее умолчание), — теперь «по профилю»
        conn.execute(
            sa.text(
                f"UPDATE reports SET {column} = NULL WHERE {column} = COALESCE("
                f"(SELECT s.{column} FROM user_settings s WHERE s.user_id = reports.user_id), {OLD_DEFAULTS[column]})"
            )
        )
        op.drop_column("user_settings", column)


def downgrade() -> None:
    op.add_column("user_settings", sa.Column("vat_multiplier", sa.Numeric(5, 3), nullable=False, server_default="1.22"))
    op.add_column("user_settings", sa.Column("tax_base", sa.String(8), nullable=False, server_default="profit"))
    op.add_column("user_settings", sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0.20"))
    conn = op.get_bind()
    # последний профиль «свой процент» — обратно в настройки
    conn.execute(
        sa.text(
            "UPDATE user_settings SET vat_multiplier = 1 + p.supplier_vat_pct / 100, "
            "tax_base = COALESCE(p.custom_base, 'profit'), tax_rate = COALESCE(p.rate_pct, 20) / 100 "
            "FROM (SELECT DISTINCT ON (user_id) user_id, supplier_vat_pct, custom_base, rate_pct FROM tax_profiles "
            "WHERE regime = 'custom' ORDER BY user_id, effective_from DESC, created_at DESC) p "
            "WHERE p.user_id = user_settings.user_id"
        )
    )
    for column in ("vat_multiplier", "tax_base", "tax_rate"):
        conn.execute(
            sa.text(
                f"UPDATE reports SET {column} = COALESCE((SELECT s.{column} FROM user_settings s "
                f"WHERE s.user_id = reports.user_id), {OLD_DEFAULTS[column]}) WHERE {column} IS NULL"
            )
        )
    op.alter_column("reports", "vat_multiplier", nullable=False, server_default="1.22")
    op.alter_column("reports", "tax_base", nullable=False, server_default="profit")
    op.alter_column("reports", "tax_rate", nullable=False, server_default="0.20")
    op.drop_index("ix_tax_profiles_user_from", table_name="tax_profiles")
    op.drop_table("tax_profiles")
