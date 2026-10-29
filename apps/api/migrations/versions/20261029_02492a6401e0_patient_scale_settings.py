"""patient scale settings

Revision ID: 02492a6401e0
Revises: 98c2d4e6f810
Create Date: 2026-10-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02492a6401e0"
down_revision: str | Sequence[str] | None = "98c2d4e6f810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add per-patient scale survey settings chosen by the doctor (#180)."""
    op.create_table(
        "patient_scale_settings",
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("scale_id", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period_days >= 1", name=op.f("ck_patient_scale_settings_period_positive")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["identity.users.id"],
            name=op.f("fk_patient_scale_settings_patient_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["identity.users.id"],
            name=op.f("fk_patient_scale_settings_updated_by_users"),
        ),
        sa.PrimaryKeyConstraint("patient_id", "scale_id", name=op.f("pk_patient_scale_settings")),
        schema="assessments",
    )


def downgrade() -> None:
    """Drop the added settings table."""
    op.drop_table("patient_scale_settings", schema="assessments")
