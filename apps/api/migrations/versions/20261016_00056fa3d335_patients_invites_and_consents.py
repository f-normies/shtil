"""patients invites and consents

Revision ID: 00056fa3d335
Revises: ae25f48ed0a2
Create Date: 2026-10-16 02:20:59.252178

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00056fa3d335"
down_revision: str | Sequence[str] | None = "ae25f48ed0a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS patients")
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["identity.users.id"], name=op.f("fk_invites_doctor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["used_by"], ["identity.users.id"], name=op.f("fk_invites_used_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invites_token_hash")),
        schema="patients",
    )
    op.create_index(
        op.f("ix_patients_invites_doctor_id"),
        "invites",
        ["doctor_id"],
        unique=False,
        schema="patients",
    )
    op.create_table(
        "patients",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("lead_doctor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lead_doctor_id"], ["identity.users.id"], name=op.f("fk_patients_lead_doctor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_patients_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_patients")),
        schema="patients",
    )
    op.create_index(
        op.f("ix_patients_patients_lead_doctor_id"),
        "patients",
        ["lead_doctor_id"],
        unique=False,
        schema="patients",
    )
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "given_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patients.user_id"],
            name=op.f("fk_consents_patient_id_patients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
        schema="patients",
    )
    op.create_index(
        op.f("ix_patients_consents_patient_id"),
        "consents",
        ["patient_id"],
        unique=False,
        schema="patients",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_patients_consents_patient_id"), table_name="consents", schema="patients")
    op.drop_table("consents", schema="patients")
    op.drop_index(
        op.f("ix_patients_patients_lead_doctor_id"), table_name="patients", schema="patients"
    )
    op.drop_table("patients", schema="patients")
    op.drop_index(op.f("ix_patients_invites_doctor_id"), table_name="invites", schema="patients")
    op.drop_table("invites", schema="patients")
    op.execute("DROP SCHEMA patients")
