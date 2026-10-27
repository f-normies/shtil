"""therapy and patient profile

Revision ID: 7d2a6c4e1b93
Revises: 5c1e9b7a2d40
Create Date: 2026-10-18 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7d2a6c4e1b93"
down_revision: str | Sequence[str] | None = "5c1e9b7a2d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("patients", sa.Column("birth_date", sa.Date(), nullable=True), schema="patients")
    op.add_column(
        "patients", sa.Column("sex", sa.String(length=8), nullable=True), schema="patients"
    )
    op.create_table(
        "diagnoses",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("icd10", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("set_by", sa.Uuid(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "icd10 ~ '^[A-Z][0-9]{2}(\\.[0-9]{1,2})?$'", name=op.f("ck_diagnoses_icd10_format")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patients.user_id"],
            name=op.f("fk_diagnoses_patient_id_patients"),
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["identity.users.id"], name=op.f("fk_diagnoses_resolved_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["identity.users.id"], name=op.f("fk_diagnoses_set_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnoses")),
        schema="patients",
    )
    op.create_index(
        op.f("ix_patients_diagnoses_patient_id"),
        "diagnoses",
        ["patient_id"],
        unique=False,
        schema="patients",
    )
    op.execute("CREATE SCHEMA IF NOT EXISTS therapy")
    op.create_table(
        "prescriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("drug_id", sa.String(length=64), nullable=False),
        sa.Column("indication_id", sa.String(length=64), nullable=False),
        sa.Column("dose_mg", sa.Float(), nullable=False),
        sa.Column("times_per_day", sa.Integer(), nullable=False),
        sa.Column("schedule", sa.String(length=200), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("off_label", sa.Boolean(), nullable=False),
        sa.Column("off_label_reason", sa.Text(), nullable=True),
        sa.Column("card_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_by", sa.Uuid(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("stop_proposal_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["identity.users.id"],
            name=op.f("fk_prescriptions_confirmed_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_prescriptions_patient_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.proposals.id"],
            name=op.f("fk_prescriptions_proposal_id_proposals"),
        ),
        sa.ForeignKeyConstraint(
            ["stop_proposal_id"],
            ["proposals.proposals.id"],
            name=op.f("fk_prescriptions_stop_proposal_id_proposals"),
        ),
        sa.ForeignKeyConstraint(
            ["stopped_by"], ["identity.users.id"], name=op.f("fk_prescriptions_stopped_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prescriptions")),
        schema="therapy",
    )
    op.create_index(
        op.f("ix_therapy_prescriptions_patient_id"),
        "prescriptions",
        ["patient_id"],
        unique=False,
        schema="therapy",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_therapy_prescriptions_patient_id"), table_name="prescriptions", schema="therapy"
    )
    op.drop_table("prescriptions", schema="therapy")
    op.execute("DROP SCHEMA IF EXISTS therapy")
    op.drop_index(
        op.f("ix_patients_diagnoses_patient_id"), table_name="diagnoses", schema="patients"
    )
    op.drop_table("diagnoses", schema="patients")
    op.drop_column("patients", "sex", schema="patients")
    op.drop_column("patients", "birth_date", schema="patients")
