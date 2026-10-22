"""Несколько магазинов у одного аккаунта (шаг 8.4b)

Магазин — строка users без входа: owner_id — аккаунт человека, store_name — название; email и пароль у неё пусты.
Аккаунт человека — его первый магазин, его данные остаются на месте. ozon_client_id — кабинет Ozon магазина: ключи
другого Client-Id в магазин больше не принимаются.

Раньше у аккаунта могло быть несколько ключей разных кабинетов Ozon (Client-Id) — синхронизация брала последний
использованный, и данные магазинов смешивались. Теперь: ключ самого раннего Client-Id остаётся у аккаунта, ключи
каждого другого Client-Id переносятся в свой новый магазин (ключи не удаляются и не расшифровываются). У аккаунта, где
так разделили ключи, сбрасываются отметки «день загружен» начислений Ozon (accrual_days): следующая синхронизация
перезагрузит их ключом этого магазина и заменит смешанные строки API (как в 0031 — это копии данных Ozon, которые он
отдаёт заново; отчёты XLSX не затрагиваются).

Revision ID: 0032_stores
Revises: 0031_accruals_by_day
Create Date: 2026-10-22
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0032_stores"
down_revision = "0031_accruals_by_day"
branch_labels = None
depends_on = None

DEFAULT_LABEL = "Магазин Ozon"  # schemas/credential.DEFAULT_LABEL


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(320), nullable=True)
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=True)
    op.add_column("users", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.add_column("users", sa.Column("store_name", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("ozon_client_id", sa.String(32), nullable=True))
    op.create_foreign_key("fk_users_owner", "users", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_users_owner_id", "users", ["owner_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT c.user_id, c.client_id, c.label, c.created_at, u.is_active FROM api_credentials c "
        "JOIN users u ON u.id = c.user_id WHERE u.owner_id IS NULL ORDER BY c.user_id, c.created_at"
    )).all()
    by_user: dict = {}
    for user_id, client_id, label, created_at, is_active in rows:
        by_user.setdefault(user_id, {"active": is_active, "clients": {}})["clients"].setdefault(client_id, label)
    now = datetime.now(timezone.utc)
    for user_id, info in by_user.items():
        clients = list(info["clients"].items())  # по дате первого ключа: первый Client-Id остаётся у аккаунта
        if len(clients) < 2:
            continue
        first_label = clients[0][1]
        if first_label and first_label != DEFAULT_LABEL:
            bind.execute(sa.text("UPDATE users SET store_name = :name WHERE id = :id"),
                         {"name": first_label[:100], "id": user_id})
        for n, (client_id, label) in enumerate(clients[1:], start=2):
            store_id = uuid.uuid4()
            name = label if label and label != DEFAULT_LABEL else f"Магазин {n}"
            bind.execute(sa.text(
                "INSERT INTO users (id, owner_id, store_name, is_active, is_verified, created_at, updated_at) "
                "VALUES (:id, :owner, :name, :active, false, :now, :now)"
            ), {"id": store_id, "owner": user_id, "name": name[:100], "active": info["active"], "now": now})
            bind.execute(sa.text("UPDATE api_credentials SET user_id = :store WHERE user_id = :user AND client_id = :client"),
                         {"store": store_id, "user": user_id, "client": client_id})
        bind.execute(sa.text("DELETE FROM accrual_days WHERE user_id = :user"), {"user": user_id})
    # кабинет каждого магазина — Client-Id его самого раннего ключа
    for user_id, client_id in bind.execute(sa.text(
        "SELECT user_id, client_id FROM api_credentials ORDER BY user_id, created_at"
    )).all():
        bind.execute(sa.text("UPDATE users SET ozon_client_id = :client WHERE id = :id AND ozon_client_id IS NULL"),
                     {"client": client_id, "id": user_id})


def downgrade() -> None:
    # ключи магазинов возвращаются аккаунту; данные магазинов удаляются каскадом вместе с ними
    op.execute("UPDATE api_credentials SET user_id = u.owner_id FROM users u WHERE u.id = api_credentials.user_id "
               "AND u.owner_id IS NOT NULL")
    op.execute("DELETE FROM users WHERE owner_id IS NOT NULL")
    op.drop_index("ix_users_owner_id", table_name="users")
    op.drop_constraint("fk_users_owner", "users", type_="foreignkey")
    op.drop_column("users", "ozon_client_id")
    op.drop_column("users", "store_name")
    op.drop_column("users", "owner_id")
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=False)
    op.alter_column("users", "email", existing_type=sa.String(320), nullable=False)
