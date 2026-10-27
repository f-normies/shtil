"""Diary entries, immutable revisions, operation receipts and intake marks.

Revision ID: a31d5c7e9b40
Revises: eba74e40fc6f
Create Date: 2026-10-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a31d5c7e9b40"
down_revision: str | Sequence[str] | None = "eba74e40fc6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the additive diary schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS diary")
    op.create_table(
        "entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("daily_on", sa.Date(), nullable=True),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('daily', 'mood', 'text', 'voice')", name=op.f("ck_entries_kind")
        ),
        sa.CheckConstraint(
            "(kind = 'daily') = (daily_on IS NOT NULL)", name=op.f("ck_entries_daily_on")
        ),
        sa.CheckConstraint("current_revision >= 1", name=op.f("ck_entries_current_revision")),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_entries_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entries")),
        sa.UniqueConstraint("patient_id", "daily_on", name="patient_daily"),
        schema="diary",
    )
    op.create_index("ix_diary_entries_patient_id", "entries", ["patient_id"], schema="diary")
    op.create_index(
        "ix_diary_entries_feed", "entries", ["patient_id", "occurred_at", "id"], schema="diary"
    )
    op.create_index("ix_diary_entries_month", "entries", ["patient_id", "daily_on"], schema="diary")

    op.create_table(
        "audio",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("transcript_source_id", sa.Uuid(), nullable=True),
        sa.Column("asr_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("byte_size > 0", name=op.f("ck_audio_byte_size_positive")),
        sa.CheckConstraint("duration_ms > 0", name=op.f("ck_audio_duration_positive")),
        sa.CheckConstraint("failures >= 0", name=op.f("ck_audio_failures_nonnegative")),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')", name=op.f("ck_audio_status")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["diary.entries.id"], name=op.f("fk_audio_entry_id_entries")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio")),
        sa.UniqueConstraint("storage_key", name="storage_key"),
        schema="diary",
    )
    op.create_index("ix_diary_audio_entry", "audio", ["entry_id"], schema="diary")

    op.create_table(
        "revisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mood_level", sa.Integer(), nullable=True),
        sa.Column(
            "mood_words",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_source_id", sa.Uuid(), nullable=True),
        sa.Column("other_medicine_text", sa.Text(), nullable=True),
        sa.Column("other_medicine_source_id", sa.Uuid(), nullable=True),
        sa.Column("audio_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_revisions_revision_positive")),
        sa.CheckConstraint(
            "mood_level IS NULL OR mood_level BETWEEN 1 AND 5", name=op.f("ck_revisions_mood_level")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["diary.entries.id"], name=op.f("fk_revisions_entry_id_entries")
        ),
        sa.ForeignKeyConstraint(
            ["audio_id"], ["diary.audio.id"], name=op.f("fk_revisions_audio_id_audio")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revisions")),
        sa.UniqueConstraint("entry_id", "revision", name="entry_revision"),
        schema="diary",
    )
    op.create_index(
        "ix_diary_revisions_entry", "revisions", ["entry_id", "revision"], schema="diary"
    )

    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_operations_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operations")),
        sa.UniqueConstraint("patient_id", "operation_id", name="patient_operation"),
        schema="diary",
    )
    op.create_index(
        "ix_diary_operations_patient", "operations", ["patient_id", "created_at"], schema="diary"
    )

    op.create_table(
        "intake_marks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("prescription_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_on", sa.Date(), nullable=False),
        sa.Column("dose_ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=24), server_default="patient_report", nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scheme_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("dose_ordinal >= 1", name=op.f("ck_intake_marks_dose_ordinal")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_intake_marks_version")),
        sa.CheckConstraint("status IN ('taken', 'unmarked')", name=op.f("ck_intake_marks_status")),
        sa.CheckConstraint("source = 'patient_report'", name=op.f("ck_intake_marks_source")),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_intake_marks_patient_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id"],
            ["therapy.prescriptions.id"],
            name=op.f("fk_intake_marks_prescription_id_prescriptions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_intake_marks")),
        sa.UniqueConstraint(
            "patient_id",
            "prescription_id",
            "scheduled_on",
            "dose_ordinal",
            "version",
            name="slot_version",
        ),
        schema="diary",
    )
    op.create_index(
        "ix_diary_intake_patient_day",
        "intake_marks",
        ["patient_id", "scheduled_on"],
        schema="diary",
    )
    op.create_index(
        "ix_diary_intake_slot",
        "intake_marks",
        ["patient_id", "prescription_id", "scheduled_on", "dose_ordinal", "version"],
        schema="diary",
    )
    op.create_table(
        "revision_intake_marks",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("intake_mark_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["diary.revisions.id"],
            name=op.f("fk_revision_intake_marks_revision_id_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["intake_mark_id"],
            ["diary.intake_marks.id"],
            name=op.f("fk_revision_intake_marks_intake_mark_id_intake_marks"),
        ),
        sa.PrimaryKeyConstraint(
            "revision_id", "intake_mark_id", name=op.f("pk_revision_intake_marks")
        ),
        schema="diary",
    )


def downgrade() -> None:
    """Drop diary tables and schema."""
    op.drop_table("revision_intake_marks", schema="diary")
    op.drop_table("intake_marks", schema="diary")
    op.drop_table("operations", schema="diary")
    op.drop_index("ix_diary_revisions_entry", table_name="revisions", schema="diary")
    op.drop_table("revisions", schema="diary")
    op.drop_index("ix_diary_audio_entry", table_name="audio", schema="diary")
    op.drop_table("audio", schema="diary")
    op.drop_index("ix_diary_entries_month", table_name="entries", schema="diary")
    op.drop_index("ix_diary_entries_feed", table_name="entries", schema="diary")
    op.drop_index("ix_diary_entries_patient_id", table_name="entries", schema="diary")
    op.drop_table("entries", schema="diary")
    op.execute("DROP SCHEMA IF EXISTS diary")
