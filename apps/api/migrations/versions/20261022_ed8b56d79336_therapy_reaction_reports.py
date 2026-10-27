"""therapy reaction reports

Revision ID: ed8b56d79336
Revises: ca0112806f01
Create Date: 2026-10-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ed8b56d79336"
down_revision: str | Sequence[str] | None = "ca0112806f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "reaction_reports",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("prescription_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("reactions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["identity.users.id"],
            name=op.f("fk_reaction_reports_patient_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id"],
            ["therapy.prescriptions.id"],
            name=op.f("fk_reaction_reports_prescription_id_prescriptions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reaction_reports")),
        schema="therapy",
    )
    for column in ("patient_id", "prescription_id"):
        op.create_index(
            op.f(f"ix_therapy_reaction_reports_{column}"),
            "reaction_reports",
            [column],
            unique=False,
            schema="therapy",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for column in ("prescription_id", "patient_id"):
        op.drop_index(
            op.f(f"ix_therapy_reaction_reports_{column}"),
            table_name="reaction_reports",
            schema="therapy",
        )
    op.drop_table("reaction_reports", schema="therapy")
