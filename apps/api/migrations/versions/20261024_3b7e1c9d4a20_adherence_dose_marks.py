"""adherence: dose marks

Revision ID: 3b7e1c9d4a20
Revises: 72e5305fe918
Create Date: 2026-10-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b7e1c9d4a20"
down_revision: str | Sequence[str] | None = "72e5305fe918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS adherence")
    op.create_table(
        "dose_marks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("prescription_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('taken', 'missed', 'cleared')", name=op.f("ck_dose_marks_status")
        ),
        sa.CheckConstraint("slot >= 0", name=op.f("ck_dose_marks_slot")),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["identity.users.id"],
            name=op.f("fk_dose_marks_patient_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id"],
            ["therapy.prescriptions.id"],
            name=op.f("fk_dose_marks_prescription_id_prescriptions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dose_marks")),
        schema="adherence",
    )
    op.create_index(
        op.f("ix_adherence_dose_marks_patient_id"),
        "dose_marks",
        ["patient_id"],
        unique=False,
        schema="adherence",
    )
    op.create_index(
        "ix_adherence_dose_marks_dose",
        "dose_marks",
        ["prescription_id", "day", "slot", "marked_at"],
        unique=False,
        schema="adherence",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_adherence_dose_marks_dose", table_name="dose_marks", schema="adherence")
    op.drop_index(
        op.f("ix_adherence_dose_marks_patient_id"), table_name="dose_marks", schema="adherence"
    )
    op.drop_table("dose_marks", schema="adherence")
    op.execute("DROP SCHEMA IF EXISTS adherence")
