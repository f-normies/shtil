"""proposals

Revision ID: 3d3f542503a5
Revises: 00056fa3d335
Create Date: 2026-10-16 02:23:25.014936

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3d3f542503a5"
down_revision: str | Sequence[str] | None = "00056fa3d335"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS proposals")
    op.create_table(
        "proposals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin_type", sa.String(length=16), nullable=False),
        sa.Column("origin_ref", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(status = 'proposed') = (decided_at IS NULL AND decided_by IS NULL)",
            name=op.f("ck_proposals_decision_matches_status"),
        ),
        sa.CheckConstraint(
            "origin_type IN ('rule', 'ai_run', 'doctor')", name=op.f("ck_proposals_origin_type")
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected')", name=op.f("ck_proposals_status")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["identity.users.id"], name=op.f("fk_proposals_decided_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_proposals_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposals")),
        schema="proposals",
    )
    op.create_index(
        op.f("ix_proposals_proposals_patient_id"),
        "proposals",
        ["patient_id"],
        unique=False,
        schema="proposals",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_proposals_proposals_patient_id"), table_name="proposals", schema="proposals"
    )
    op.drop_table("proposals", schema="proposals")
    op.execute("DROP SCHEMA proposals")
