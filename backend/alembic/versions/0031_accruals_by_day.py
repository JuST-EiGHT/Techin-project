"""Начисления из API по реальному формату Ozon: код типа в справочнике, перезагрузка начислений за 90 дней

Ozon в справочнике типов отдаёт английский код в name и русское название в description: код — в новую колонку
accrual_types.code, русское название — в name_ru. Выручка и баллы приходят в записи отправления (by-day), а не типом —
прежний разбор их не видел, а запрос по отправлениям (/postings) падал на номерах не того вида. Начисления из API
загружаются заново (services/sync/finance.py): отметки «день загружен» (accrual_days) сбрасываются — следующая
синхронизация перезагрузит 90 дней, строки прежнего разбора по отправлениям (области posting:*, posting-day:*)
удаляются. Это копии данных Ozon, которые он отдаёт заново за любой день с 2022 года; отчёты XLSX не затрагиваются.

Revision ID: 0031_accruals_by_day
Revises: 0030_acquiring_rub
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_accruals_by_day"
down_revision = "0030_acquiring_rub"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accrual_types", sa.Column("code", sa.String(100)))
    op.execute(
        "UPDATE accrual_types SET code = name_ru, name_ru = left(description, 255) "
        "WHERE description IS NOT NULL AND description <> ''"
    )
    op.execute("DELETE FROM accruals WHERE source = 'api' AND (sync_scope LIKE 'posting:%' OR sync_scope LIKE 'posting-day:%')")
    op.execute("DELETE FROM accrual_days")


def downgrade() -> None:
    # русские названия остаются в name_ru — разбор старой версии кода их не требует
    op.drop_column("accrual_types", "code")
