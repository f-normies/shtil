"""messaging conversations and messages

Revision ID: c33d92be60e1
Revises: 9e4b1d7c3a52
Create Date: 2026-10-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c33d92be60e1"
down_revision: str | Sequence[str] | None = "9e4b1d7c3a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial conversation and message tables."""
    op.execute("CREATE SCHEMA IF NOT EXISTS messaging")
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("last_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "doctor_read_through_seq", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "patient_read_through_seq", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("last_seq >= 0", name=op.f("ck_conversations_last_seq_nonnegative")),
        sa.CheckConstraint(
            "doctor_read_through_seq >= 0",
            name=op.f("ck_conversations_doctor_read_nonnegative"),
        ),
        sa.CheckConstraint(
            "patient_read_through_seq >= 0",
            name=op.f("ck_conversations_patient_read_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["identity.users.id"], name=op.f("fk_conversations_doctor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patients.user_id"],
            name=op.f("fk_conversations_patient_id_patients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("patient_id", "doctor_id", name="uq_messaging_conversations_pair"),
        schema="messaging",
    )
    op.create_index(
        op.f("ix_messaging_conversations_doctor_id"),
        "conversations",
        ["doctor_id"],
        unique=False,
        schema="messaging",
    )
    op.create_index(
        op.f("ix_messaging_conversations_patient_id"),
        "conversations",
        ["patient_id"],
        unique=False,
        schema="messaging",
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("client_message_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("audio_key", sa.String(length=512), nullable=True),
        sa.Column("audio_mime", sa.String(length=64), nullable=True),
        sa.Column("audio_sha256", sa.String(length=64), nullable=True),
        sa.Column("audio_size", sa.Integer(), nullable=True),
        sa.Column("transcription_status", sa.String(length=16), nullable=True),
        sa.Column("transcription_text", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("asr_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "transcription_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("transcription_error_code", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('text', 'voice')", name=op.f("ck_messages_kind")),
        sa.CheckConstraint(
            "(kind = 'text' AND text IS NOT NULL AND audio_key IS NULL) OR "
            "(kind = 'voice' AND text IS NULL AND audio_key IS NOT NULL)",
            name=op.f("ck_messages_content_matches_kind"),
        ),
        sa.CheckConstraint(
            "transcription_status IS NULL OR transcription_status IN "
            "('queued', 'processing', 'done', 'failed')",
            name=op.f("ck_messages_transcription_status"),
        ),
        sa.CheckConstraint(
            "transcription_error_code IS NULL OR transcription_error_code IN "
            "('asr_rejected', 'audio_missing', 'transcription_failed')",
            name=op.f("ck_messages_transcription_error_code"),
        ),
        sa.CheckConstraint(
            "transcription_failures >= 0",
            name=op.f("ck_messages_transcription_failures_nonnegative"),
        ),
        sa.CheckConstraint(
            "audio_size IS NULL OR audio_size > 0", name=op.f("ck_messages_audio_size")
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name=op.f("ck_messages_duration_ms")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["messaging.conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["identity.users.id"], name=op.f("fk_messages_sender_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "conversation_id", "seq", name="uq_messaging_messages_conversation_seq"
        ),
        sa.UniqueConstraint(
            "sender_id", "client_message_id", name="uq_messaging_messages_sender_client"
        ),
        schema="messaging",
    )
    op.create_index(
        "ix_messaging_messages_unread",
        "messages",
        ["conversation_id", "sender_id", "seq"],
        unique=False,
        schema="messaging",
    )


def downgrade() -> None:
    """Drop messaging tables and schema."""
    op.drop_table("messages", schema="messaging")
    op.drop_table("conversations", schema="messaging")
    op.execute("DROP SCHEMA IF EXISTS messaging")
