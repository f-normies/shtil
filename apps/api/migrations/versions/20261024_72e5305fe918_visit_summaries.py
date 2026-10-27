"""visit summaries (#38)

Revision ID: 72e5305fe918
Revises: ef2ff6142a10
Create Date: 2026-10-24 10:01:49.925680

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "72e5305fe918"
down_revision: str | Sequence[str] | None = "ef2ff6142a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS summary")
    op.create_table(
        "visit_summaries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dropped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_statements", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'confirmed') = (confirmed_statements IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name=op.f("ck_visit_summaries_confirmed_complete"),
        ),
        sa.CheckConstraint(
            "(status IN ('done', 'confirmed')) = (draft IS NOT NULL AND computed_at IS NOT NULL)",
            name=op.f("ck_visit_summaries_draft_ready"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed', 'confirmed')",
            name=op.f("ck_visit_summaries_status"),
        ),
        sa.CheckConstraint(
            "failures >= 0 AND dropped >= 0", name=op.f("ck_visit_summaries_counters")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["identity.users.id"],
            name=op.f("fk_visit_summaries_confirmed_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_visit_summaries_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_summaries")),
        sa.UniqueConstraint("data_key", name=op.f("uq_visit_summaries_data_key")),
        schema="summary",
    )
    op.create_index(
        op.f("ix_summary_visit_summaries_patient_id"),
        "visit_summaries",
        ["patient_id"],
        unique=False,
        schema="summary",
    )
    op.create_index(
        "uq_visit_summaries_confirmed_period",
        "visit_summaries",
        ["patient_id", "period_start"],
        unique=True,
        schema="summary",
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_visit_summaries_confirmed_period",
        table_name="visit_summaries",
        schema="summary",
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.drop_index(
        op.f("ix_summary_visit_summaries_patient_id"),
        table_name="visit_summaries",
        schema="summary",
    )
    op.drop_table("visit_summaries", schema="summary")
    op.execute("DROP SCHEMA summary")
