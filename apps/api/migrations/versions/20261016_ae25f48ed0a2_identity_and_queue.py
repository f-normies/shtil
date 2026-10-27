"""identity and queue

Схема identity (пользователи, сессии, журнал доступа) и очередь PgQueuer в схеме pgq.

Revision ID: ae25f48ed0a2
Revises:
Create Date: 2026-10-16 02:16:41.980917

"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# SQL установки PgQueuer 1.6.0 (`pgq --schema pgq sql install`). Обновление версии —
# новая миграция по `pgq upgrade --plan`.
PGQUEUER_SQL = Path(__file__).parents[1] / "sql" / "pgqueuer_1.6.0_install.sql"


# revision identifiers, used by Alembic.
revision: str = "ae25f48ed0a2"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _execute_script(sql: str) -> None:
    """Скрипт из нескольких команд: через asyncpg напрямую (простой протокол, без prepare)."""
    dbapi_connection = op.get_bind().connection.dbapi_connection
    dbapi_connection.run_async(lambda conn: conn.execute(sql))


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")
    _execute_script(PGQUEUER_SQL.read_text())

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('doctor', 'patient', 'admin')", name=op.f("ck_users_role")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        schema="identity",
    )
    op.create_table(
        "access_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["identity.users.id"], name=op.f("fk_access_log_actor_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_log")),
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_access_log_actor_id"),
        "access_log",
        ["actor_id"],
        unique=False,
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_access_log_patient_id"),
        "access_log",
        ["patient_id"],
        unique=False,
        schema="identity",
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_sessions")),
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_sessions_user_id"),
        "sessions",
        ["user_id"],
        unique=False,
        schema="identity",
    )

    # Журнал доступа только дописывается: правка и удаление строк запрещены.
    op.execute("""
        CREATE FUNCTION identity.forbid_access_log_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'identity.access_log is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER access_log_append_only
        BEFORE UPDATE OR DELETE ON identity.access_log
        FOR EACH ROW EXECUTE FUNCTION identity.forbid_access_log_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER access_log_append_only ON identity.access_log")
    op.execute("DROP FUNCTION identity.forbid_access_log_change()")
    op.drop_index(op.f("ix_identity_sessions_user_id"), table_name="sessions", schema="identity")
    op.drop_table("sessions", schema="identity")
    op.drop_index(
        op.f("ix_identity_access_log_patient_id"), table_name="access_log", schema="identity"
    )
    op.drop_index(
        op.f("ix_identity_access_log_actor_id"), table_name="access_log", schema="identity"
    )
    op.drop_table("access_log", schema="identity")
    op.drop_table("users", schema="identity")
    op.execute("DROP SCHEMA identity")
    op.execute("DROP SCHEMA pgq CASCADE")
