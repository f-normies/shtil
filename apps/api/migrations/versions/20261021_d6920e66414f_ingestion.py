"""ingestion: documents and confirmed facts

Revision ID: d6920e66414f
Revises: 85f89b2e0af7
Create Date: 2026-10-21 23:55:16.519651

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d6920e66414f"
down_revision: str | Sequence[str] | None = "85f89b2e0af7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS ingestion")
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=200), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="parsing", nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_page", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "page_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("ocr", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extract_run_id", sa.Uuid(), nullable=True),
        sa.Column("dropped", sa.Integer(), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column(
            "known", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("parse_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extract_failures", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)", name=op.f("ck_documents_error_status")
        ),
        sa.CheckConstraint(
            "content_type IN ('application/pdf', 'image/jpeg', 'image/png')",
            name=op.f("ck_documents_content_type"),
        ),
        sa.CheckConstraint(
            "status IN ('parsing', 'extracting', 'done', 'failed')",
            name=op.f("ck_documents_status"),
        ),
        sa.CheckConstraint(
            "(extracted_at IS NULL) = (extract_run_id IS NULL)",
            name=op.f("ck_documents_extraction_complete"),
        ),
        sa.CheckConstraint(
            "parse_failures >= 0 AND extract_failures >= 0", name=op.f("ck_documents_failures")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_documents_patient_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["identity.users.id"], name=op.f("fk_documents_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("patient_id", "sha256", name=op.f("uq_documents_patient_id")),
        schema="ingestion",
    )
    op.create_table(
        "facts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "field IN ('anamnesis', 'risk_factor', 'past_medication')", name=op.f("ck_facts_field")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["identity.users.id"], name=op.f("fk_facts_confirmed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["ingestion.documents.id"], name=op.f("fk_facts_document_id_documents")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_facts_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facts")),
        sa.UniqueConstraint("proposal_id", name=op.f("uq_facts_proposal_id")),
        schema="ingestion",
    )
    op.create_index(
        op.f("ix_ingestion_facts_document_id"),
        "facts",
        ["document_id"],
        unique=False,
        schema="ingestion",
    )
    op.create_index(
        op.f("ix_ingestion_facts_patient_id"),
        "facts",
        ["patient_id"],
        unique=False,
        schema="ingestion",
    )
    # Медицинские данные физически не удаляются. Документ можно дополнить итогами этапов,
    # удалить нельзя. Подтверждённые факты только дописываются: UPDATE, DELETE и TRUNCATE
    # запрещены. Тесты очищают таблицы с session_replication_role = replica.
    op.execute("""
        CREATE FUNCTION ingestion.forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '%.% keeps medical data: % is forbidden',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER documents_no_delete
        BEFORE DELETE ON ingestion.documents
        FOR EACH ROW EXECUTE FUNCTION ingestion.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER documents_no_truncate
        BEFORE TRUNCATE ON ingestion.documents
        FOR EACH STATEMENT EXECUTE FUNCTION ingestion.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER facts_append_only
        BEFORE UPDATE OR DELETE ON ingestion.facts
        FOR EACH ROW EXECUTE FUNCTION ingestion.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER facts_no_truncate
        BEFORE TRUNCATE ON ingestion.facts
        FOR EACH STATEMENT EXECUTE FUNCTION ingestion.forbid_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER facts_no_truncate ON ingestion.facts")
    op.execute("DROP TRIGGER facts_append_only ON ingestion.facts")
    op.execute("DROP TRIGGER documents_no_truncate ON ingestion.documents")
    op.execute("DROP TRIGGER documents_no_delete ON ingestion.documents")
    op.execute("DROP FUNCTION ingestion.forbid_change()")
    op.drop_index(op.f("ix_ingestion_facts_patient_id"), table_name="facts", schema="ingestion")
    op.drop_index(op.f("ix_ingestion_facts_document_id"), table_name="facts", schema="ingestion")
    op.drop_table("facts", schema="ingestion")
    op.drop_table("documents", schema="ingestion")
    op.execute("DROP SCHEMA ingestion")
