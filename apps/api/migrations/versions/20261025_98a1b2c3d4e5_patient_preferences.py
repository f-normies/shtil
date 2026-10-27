"""patient preferences

Revision ID: 98a1b2c3d4e5
Revises: 85a6096aaf03
Create Date: 2026-10-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "98a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "a31d5c7e9b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add one preferences row per patient, with optimistic revision state."""
    op.create_table(
        "preferences",
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "reminder_time",
            sa.Time(),
            server_default=sa.text("'21:00:00'"),
            nullable=False,
        ),
        sa.Column(
            "mood_palette", sa.String(length=16), server_default=sa.text("'warm'"), nullable=False
        ),
        sa.Column(
            "story_mode", sa.String(length=16), server_default=sa.text("'voice'"), nullable=False
        ),
        sa.Column(
            "text_size", sa.String(length=16), server_default=sa.text("'normal'"), nullable=False
        ),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(display_name) <> ''", name=op.f("ck_preferences_display_name_nonempty")
        ),
        sa.CheckConstraint(
            "EXTRACT(MINUTE FROM reminder_time) IN (0, 15, 30, 45) "
            "AND EXTRACT(SECOND FROM reminder_time) = 0",
            name=op.f("ck_preferences_reminder_time_quarter_hour"),
        ),
        sa.CheckConstraint(
            "mood_palette IN ('warm', 'green', 'calm')",
            name=op.f("ck_preferences_mood_palette_values"),
        ),
        sa.CheckConstraint(
            "story_mode IN ('voice', 'text')", name=op.f("ck_preferences_story_mode_values")
        ),
        sa.CheckConstraint(
            "text_size IN ('normal', 'large', 'extra_large')",
            name=op.f("ck_preferences_text_size_values"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_preferences_revision_positive")),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patients.user_id"],
            name=op.f("fk_preferences_patient_id_patients"),
        ),
        sa.PrimaryKeyConstraint("patient_id", name=op.f("pk_preferences")),
        schema="patients",
    )


def downgrade() -> None:
    """Drop the added preferences table."""
    op.drop_table("preferences", schema="patients")
