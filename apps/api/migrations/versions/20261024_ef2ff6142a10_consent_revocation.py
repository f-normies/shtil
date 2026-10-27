"""identity и patients: отзыв согласия и закрытый вход (#112).

Только добавляет колонки и проверку «время и причина отключения — вместе»: у прежних строк
NULL — согласие действует, вход открыт.

Revision ID: ef2ff6142a10
Revises: 85a6096aaf03
Create Date: 2026-10-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef2ff6142a10"
down_revision: str | Sequence[str] | None = "85a6096aaf03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        schema="identity",
    )
    op.add_column(
        "users",
        sa.Column("disabled_reason", sa.String(length=32), nullable=True),
        schema="identity",
    )
    op.create_check_constraint(
        op.f("ck_users_disabled_complete"),
        "users",
        "(disabled_at IS NULL) = (disabled_reason IS NULL)",
        schema="identity",
    )
    op.add_column(
        "consents",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema="patients",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("consents", "revoked_at", schema="patients")
    op.drop_constraint(
        op.f("ck_users_disabled_complete"), "users", type_="check", schema="identity"
    )
    op.drop_column("users", "disabled_reason", schema="identity")
    op.drop_column("users", "disabled_at", schema="identity")
