"""assessments

Revision ID: a90d24c5e6f1
Revises: 8309abae9e22
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a90d24c5e6f1"
down_revision: str | Sequence[str] | None = "8309abae9e22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only assessment result table."""
    op.execute("CREATE SCHEMA IF NOT EXISTS assessments")
    op.create_table(
        "results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("scale_id", sa.String(length=200), nullable=False),
        sa.Column("scale_version", sa.Integer(), nullable=False),
        sa.Column("scale_title", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["identity.users.id"],
            name=op.f("fk_results_patient_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_results")),
        sa.UniqueConstraint(
            "patient_id",
            "submission_id",
            name="uq_assessments_results_patient_submission",
        ),
        schema="assessments",
    )
    op.create_index(
        "ix_assessments_results_history",
        "results",
        ["patient_id", "id"],
        unique=False,
        schema="assessments",
    )
    op.create_index(
        "ix_assessments_results_definition",
        "results",
        ["scale_id", "scale_version", "id"],
        unique=False,
        schema="assessments",
    )

    # Результаты только дописываются. asyncpg выполняет одну команду за вызов, поэтому
    # функция и каждый триггер создаются отдельным op.execute. Тесты очищают таблицы
    # с session_replication_role = replica, и триггеры при этом не срабатывают.
    op.execute("""
        CREATE FUNCTION assessments.forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '%.% is append-only', TG_TABLE_SCHEMA, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER results_append_only
        BEFORE UPDATE OR DELETE ON assessments.results
        FOR EACH ROW EXECUTE FUNCTION assessments.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER results_no_truncate
        BEFORE TRUNCATE ON assessments.results
        FOR EACH STATEMENT EXECUTE FUNCTION assessments.forbid_change()
    """)


def downgrade() -> None:
    """Remove the table and schema created by this revision."""
    op.execute("DROP TRIGGER results_no_truncate ON assessments.results")
    op.execute("DROP TRIGGER results_append_only ON assessments.results")
    op.execute("DROP FUNCTION assessments.forbid_change()")
    op.drop_index("ix_assessments_results_definition", table_name="results", schema="assessments")
    op.drop_index("ix_assessments_results_history", table_name="results", schema="assessments")
    op.drop_table("results", schema="assessments")
    op.execute("DROP SCHEMA assessments")
