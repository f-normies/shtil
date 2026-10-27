"""store per-prescription appearance preferences

Revision ID: 98c2d4e6f810
Revises: 98a1b2c3d4e5
Create Date: 2026-10-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "98c2d4e6f810"
down_revision: str | Sequence[str] | None = "98a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add patient-selected appearance and its optimistic revision per prescription."""
    op.create_table(
        "prescription_appearances",
        sa.Column("prescription_id", sa.Uuid(), nullable=False),
        sa.Column("shape", sa.String(length=16), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "(shape IS NULL AND color IS NULL) OR (shape IS NOT NULL AND color IS NOT NULL)",
            name=op.f("ck_prescription_appearances_shape_and_color_together"),
        ),
        sa.CheckConstraint(
            "shape IS NULL OR shape IN ('round', 'oval', 'capsule')",
            name=op.f("ck_prescription_appearances_shape_values"),
        ),
        sa.CheckConstraint(
            "color IS NULL OR color IN ('white', 'sand', 'peach', 'blue')",
            name=op.f("ck_prescription_appearances_color_values"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_prescription_appearances_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id"],
            ["therapy.prescriptions.id"],
            name=op.f("fk_prescription_appearances_prescription_id_prescriptions"),
        ),
        sa.PrimaryKeyConstraint("prescription_id", name=op.f("pk_prescription_appearances")),
        schema="therapy",
    )


def downgrade() -> None:
    """Drop the additive appearance preference table."""
    op.drop_table("prescription_appearances", schema="therapy")
