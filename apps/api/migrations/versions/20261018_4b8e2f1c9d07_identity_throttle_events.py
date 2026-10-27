"""identity throttle events

Revision ID: 4b8e2f1c9d07
Revises: 7d2a6c4e1b93
Create Date: 2026-10-18 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b8e2f1c9d07"
down_revision: str | Sequence[str] | None = "7d2a6c4e1b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "throttle_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_throttle_events")),
        schema="identity",
    )
    op.create_index(
        "ix_identity_throttle_events_bucket_key_at",
        "throttle_events",
        ["bucket", "key_hash", "at"],
        unique=False,
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_throttle_events_at"),
        "throttle_events",
        ["at"],
        unique=False,
        schema="identity",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_identity_throttle_events_at"), table_name="throttle_events", schema="identity"
    )
    op.drop_index(
        "ix_identity_throttle_events_bucket_key_at",
        table_name="throttle_events",
        schema="identity",
    )
    op.drop_table("throttle_events", schema="identity")
