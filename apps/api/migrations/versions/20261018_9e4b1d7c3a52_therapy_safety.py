"""therapy safety

Revision ID: 9e4b1d7c3a52
Revises: 59e0bc9898f3
Create Date: 2026-10-18 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9e4b1d7c3a52"
down_revision: str | Sequence[str] | None = "59e0bc9898f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    for column in (
        sa.Column("pregnant", sa.Boolean(), nullable=True),
        sa.Column("breastfeeding", sa.Boolean(), nullable=True),
        sa.Column("egfr", sa.Float(), nullable=True),
        sa.Column("egfr_measured_on", sa.Date(), nullable=True),
        sa.Column("child_pugh", sa.String(length=8), nullable=True),
    ):
        op.add_column("patients", column, schema="patients")
    op.add_column(
        "prescriptions",
        sa.Column(
            "safety_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        schema="therapy",
    )
    op.create_table(
        "medications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("inn", sa.String(length=200), nullable=True),
        sa.Column("registry_check", sa.String(length=16), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("stopped_on", sa.Date(), nullable=True),
        sa.Column("entered_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entered_by"], ["identity.users.id"], name=op.f("fk_medications_entered_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_medications_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medications")),
        schema="therapy",
    )
    op.create_index(
        op.f("ix_therapy_medications_patient_id"),
        "medications",
        ["patient_id"],
        unique=False,
        schema="therapy",
    )
    op.drop_constraint(op.f("ck_alerts_kind"), "alerts", schema="alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_alerts_kind"), "alerts", "kind IN ('alert', 'routing', 'safety')", schema="alerts"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("ck_alerts_kind"), "alerts", schema="alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_alerts_kind"), "alerts", "kind IN ('alert', 'routing')", schema="alerts"
    )
    op.drop_index(
        op.f("ix_therapy_medications_patient_id"), table_name="medications", schema="therapy"
    )
    op.drop_table("medications", schema="therapy")
    op.drop_column("prescriptions", "safety_state", schema="therapy")
    for column in ("child_pugh", "egfr_measured_on", "egfr", "breastfeeding", "pregnant"):
        op.drop_column("patients", column, schema="patients")
