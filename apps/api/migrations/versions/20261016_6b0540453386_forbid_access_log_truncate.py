"""forbid access log truncate

Revision ID: 6b0540453386
Revises: 3d3f542503a5
Create Date: 2026-10-16 03:06:48.464612

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b0540453386"
down_revision: str | Sequence[str] | None = "3d3f542503a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Журнал доступа только дописывается: TRUNCATE тоже запрещён (UPDATE и DELETE — с первой
    # миграции). Тесты очищают таблицы с session_replication_role = replica.
    op.execute("""
        CREATE TRIGGER access_log_no_truncate
        BEFORE TRUNCATE ON identity.access_log
        FOR EACH STATEMENT EXECUTE FUNCTION identity.forbid_access_log_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER access_log_no_truncate ON identity.access_log")
