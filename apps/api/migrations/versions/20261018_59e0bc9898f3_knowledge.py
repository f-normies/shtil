"""knowledge: документы и фрагменты для RAG (спека #27)

Revision ID: 59e0bc9898f3
Revises: 4b8e2f1c9d07
Create Date: 2026-10-18 01:09:11.571863

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "59e0bc9898f3"
down_revision: str | Sequence[str] | None = "4b8e2f1c9d07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # halfvec(2048) и индекс HNSW дают pgvector: тип vector индексируется только до 2000 измерений.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'superseded') = (superseded_at IS NOT NULL)",
            name=op.f("ck_documents_superseded_complete"),
        ),
        sa.CheckConstraint(
            "kind IN ('clinical_guideline', 'drug_label')", name=op.f("ck_documents_kind")
        ),
        sa.CheckConstraint("status IN ('active', 'superseded')", name=op.f("ck_documents_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        schema="knowledge",
    )
    op.create_index(
        "uq_documents_active_source_id",
        "documents",
        ["source_id"],
        unique=True,
        schema="knowledge",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "fragments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("uur", sa.Text(), nullable=True),
        sa.Column("udd", sa.SmallInteger(), nullable=True),
        sa.Column("embedding", HALFVEC(dim=2048), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('russian'::regconfig, heading_path || ' '::text || text)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind = 'recommendation' OR (uur IS NULL AND udd IS NULL)",
            name=op.f("ck_fragments_levels"),
        ),
        sa.CheckConstraint(
            "kind IN ('recommendation', 'text', 'table')", name=op.f("ck_fragments_kind")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge.documents.id"],
            name=op.f("fk_fragments_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fragments")),
        sa.UniqueConstraint("document_id", "key", name=op.f("uq_fragments_document_id")),
        schema="knowledge",
    )
    op.create_index(
        "ix_knowledge_fragments_embedding",
        "fragments",
        ["embedding"],
        unique=False,
        schema="knowledge",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )
    op.create_index(
        "ix_knowledge_fragments_tsv",
        "fragments",
        ["tsv"],
        unique=False,
        schema="knowledge",
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_knowledge_fragments_tsv",
        table_name="fragments",
        schema="knowledge",
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_knowledge_fragments_embedding",
        table_name="fragments",
        schema="knowledge",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )
    op.drop_table("fragments", schema="knowledge")
    op.drop_index(
        "uq_documents_active_source_id",
        table_name="documents",
        schema="knowledge",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("documents", schema="knowledge")
    # Расширение vector не удаляем: оно может понадобиться не только этой схеме.
    op.execute("DROP SCHEMA IF EXISTS knowledge")
