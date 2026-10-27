"""therapy guidance

Revision ID: 85f89b2e0af7
Revises: 73be2f9a0c14
Create Date: 2026-10-21 23:56:58.261159

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "85f89b2e0af7"
down_revision: str | Sequence[str] | None = "73be2f9a0c14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "guidance",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'done') = (answer IS NOT NULL AND computed_at IS NOT NULL)",
            name=op.f("ck_guidance_done_has_answer"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed')", name=op.f("ck_guidance_status")
        ),
        sa.CheckConstraint("failures >= 0", name=op.f("ck_guidance_failures")),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_guidance_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guidance")),
        sa.UniqueConstraint("patient_id", "profile_key", name=op.f("uq_guidance_patient_id")),
        schema="therapy",
    )
    op.create_index(
        op.f("ix_therapy_guidance_profile_key"),
        "guidance",
        ["profile_key"],
        unique=False,
        schema="therapy",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_therapy_guidance_profile_key"), table_name="guidance", schema="therapy")
    op.drop_table("guidance", schema="therapy")
