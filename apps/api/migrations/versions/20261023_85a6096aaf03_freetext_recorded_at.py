"""freetext: время записи у разбора (#96).

Только добавляет колонку. У прежних разборов NULL: время записи — их `created_at`.

Revision ID: 85a6096aaf03
Revises: 407adb8a1dfb
Create Date: 2026-10-23 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85a6096aaf03"
down_revision: str | Sequence[str] | None = "407adb8a1dfb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "analyses",
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        schema="freetext",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("analyses", "recorded_at", schema="freetext")
