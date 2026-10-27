"""ai runs: журнал AI-вызовов (спека #18)

Revision ID: 81f8eed538c7
Revises: a90d24c5e6f1
Create Date: 2026-10-17 17:18:22.496060

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "81f8eed538c7"
down_revision: str | Sequence[str] | None = "a90d24c5e6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS ai")
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("node", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("audio_seconds", sa.Float(), nullable=True),
        sa.CheckConstraint("status IN ('ok', 'error')", name=op.f("ck_ai_runs_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runs")),
        schema="ai",
    )
    op.create_index(
        op.f("ix_ai_ai_runs_created_at"), "ai_runs", ["created_at"], unique=False, schema="ai"
    )

    # Журнал только дописывается: на строку ссылается предложение (origin_ref). Правка, удаление
    # и TRUNCATE запрещены, как у identity.access_log. Тесты очищают таблицы
    # с session_replication_role = replica.
    op.execute("""
        CREATE FUNCTION ai.forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '%.% is append-only', TG_TABLE_SCHEMA, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER ai_runs_append_only
        BEFORE UPDATE OR DELETE ON ai.ai_runs
        FOR EACH ROW EXECUTE FUNCTION ai.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER ai_runs_no_truncate
        BEFORE TRUNCATE ON ai.ai_runs
        FOR EACH STATEMENT EXECUTE FUNCTION ai.forbid_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER ai_runs_no_truncate ON ai.ai_runs")
    op.execute("DROP TRIGGER ai_runs_append_only ON ai.ai_runs")
    op.execute("DROP FUNCTION ai.forbid_change()")
    op.drop_index(op.f("ix_ai_ai_runs_created_at"), table_name="ai_runs", schema="ai")
    op.drop_table("ai_runs", schema="ai")
    op.execute("DROP SCHEMA IF EXISTS ai")
