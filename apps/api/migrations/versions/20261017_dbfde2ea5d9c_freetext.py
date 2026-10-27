"""freetext

Revision ID: dbfde2ea5d9c
Revises: 81f8eed538c7
Create Date: 2026-10-17 21:17:53.341077

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "dbfde2ea5d9c"
down_revision: str | Sequence[str] | None = "81f8eed538c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS freetext")
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("transcript", sa.Boolean(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("risk_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risk_run_id", sa.Uuid(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extract_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "risk_signs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "alerts", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("risk_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extract_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("risk_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extract_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('diary', 'message')", name=op.f("ck_analyses_source_type")
        ),
        sa.CheckConstraint(
            "(extracted_at IS NULL) = (extract_run_id IS NULL)",
            name=op.f("ck_analyses_extraction_complete"),
        ),
        sa.CheckConstraint(
            "(risk_checked_at IS NULL) = (risk_run_id IS NULL)",
            name=op.f("ck_analyses_risk_check_complete"),
        ),
        sa.CheckConstraint(
            "risk_failures >= 0 AND extract_failures >= 0", name=op.f("ck_analyses_failures")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_analyses_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
        sa.UniqueConstraint("source_type", "source_id", name=op.f("uq_analyses_source_type")),
        schema="freetext",
    )
    op.create_index(
        op.f("ix_freetext_analyses_patient_id"),
        "analyses",
        ["patient_id"],
        unique=False,
        schema="freetext",
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "category IN ('symptom', 'side_effect', 'adherence', 'medication')",
            name=op.f("ck_findings_category"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["freetext.analyses.id"], name=op.f("fk_findings_analysis_id_analyses")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["identity.users.id"], name=op.f("fk_findings_confirmed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_findings_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint("proposal_id", name=op.f("uq_findings_proposal_id")),
        schema="freetext",
    )
    op.create_index(
        op.f("ix_freetext_findings_analysis_id"),
        "findings",
        ["analysis_id"],
        unique=False,
        schema="freetext",
    )
    op.create_index(
        op.f("ix_freetext_findings_patient_id"),
        "findings",
        ["patient_id"],
        unique=False,
        schema="freetext",
    )
    # Медицинские данные физически не удаляются. Разбор можно дополнить итогами этапов,
    # удалить нельзя. Подтверждённые находки только дописываются: UPDATE, DELETE и TRUNCATE
    # запрещены. Тесты очищают таблицы с session_replication_role = replica.
    op.execute("""
        CREATE FUNCTION freetext.forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '%.% keeps medical data: % is forbidden',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER analyses_no_delete
        BEFORE DELETE ON freetext.analyses
        FOR EACH ROW EXECUTE FUNCTION freetext.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER analyses_no_truncate
        BEFORE TRUNCATE ON freetext.analyses
        FOR EACH STATEMENT EXECUTE FUNCTION freetext.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER findings_append_only
        BEFORE UPDATE OR DELETE ON freetext.findings
        FOR EACH ROW EXECUTE FUNCTION freetext.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER findings_no_truncate
        BEFORE TRUNCATE ON freetext.findings
        FOR EACH STATEMENT EXECUTE FUNCTION freetext.forbid_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER findings_no_truncate ON freetext.findings")
    op.execute("DROP TRIGGER findings_append_only ON freetext.findings")
    op.execute("DROP TRIGGER analyses_no_truncate ON freetext.analyses")
    op.execute("DROP TRIGGER analyses_no_delete ON freetext.analyses")
    op.execute("DROP FUNCTION freetext.forbid_change()")
    op.drop_index(op.f("ix_freetext_findings_patient_id"), table_name="findings", schema="freetext")
    op.drop_index(
        op.f("ix_freetext_findings_analysis_id"), table_name="findings", schema="freetext"
    )
    op.drop_table("findings", schema="freetext")
    op.drop_index(op.f("ix_freetext_analyses_patient_id"), table_name="analyses", schema="freetext")
    op.drop_table("analyses", schema="freetext")
    op.execute("DROP SCHEMA freetext")
