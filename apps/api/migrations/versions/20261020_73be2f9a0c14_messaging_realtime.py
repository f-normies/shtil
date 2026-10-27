"""messaging leases, last-seen timestamps, and typing state

Revision ID: 73be2f9a0c14
Revises: c33d92be60e1
Create Date: 2026-10-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73be2f9a0c14"
down_revision: str | Sequence[str] | None = "c33d92be60e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ephemeral connection leases and per-pair typing records."""
    op.create_table(
        "presence_leases",
        sa.Column("connection_id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_presence_leases_user_id_users")
        ),
        sa.PrimaryKeyConstraint("connection_id", name=op.f("pk_presence_leases")),
        schema="messaging",
    )
    op.create_index(
        "ix_messaging_presence_leases_user_expiry",
        "presence_leases",
        ["user_id", "lease_until"],
        unique=False,
        schema="messaging",
    )
    op.create_index(
        "ix_messaging_presence_leases_session",
        "presence_leases",
        ["session_key"],
        unique=False,
        schema="messaging",
    )
    op.create_table(
        "user_presence",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_user_presence_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_presence")),
        schema="messaging",
    )
    op.create_table(
        "typing_states",
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "user_id = patient_id OR user_id = doctor_id",
            name=op.f("ck_typing_states_user_in_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messaging.presence_leases.connection_id"],
            name=op.f("fk_typing_states_connection_id_presence_leases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patients.user_id"],
            name=op.f("fk_typing_states_patient_id_patients"),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["identity.users.id"],
            name=op.f("fk_typing_states_doctor_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_typing_states_user_id_users")
        ),
        sa.PrimaryKeyConstraint(
            "connection_id", "patient_id", "doctor_id", name=op.f("pk_typing_states")
        ),
        schema="messaging",
    )
    op.create_index(
        "ix_messaging_typing_pair_expiry",
        "typing_states",
        ["patient_id", "doctor_id", "expires_at"],
        unique=False,
        schema="messaging",
    )


def downgrade() -> None:
    """Drop ephemeral realtime state."""
    op.drop_index("ix_messaging_typing_pair_expiry", table_name="typing_states", schema="messaging")
    op.drop_table("typing_states", schema="messaging")
    op.drop_table("user_presence", schema="messaging")
    op.drop_index(
        "ix_messaging_presence_leases_session", table_name="presence_leases", schema="messaging"
    )
    op.drop_index(
        "ix_messaging_presence_leases_user_expiry",
        table_name="presence_leases",
        schema="messaging",
    )
    op.drop_table("presence_leases", schema="messaging")
