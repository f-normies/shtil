"""patients phone

Revision ID: 5c1e9b7a2d40
Revises: dbfde2ea5d9c
Create Date: 2026-10-18 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1e9b7a2d40"
down_revision: str | Sequence[str] | None = "dbfde2ea5d9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "patients",
        sa.Column("phone", sa.String(length=16), nullable=True),
        schema="patients",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("patients", "phone", schema="patients")
