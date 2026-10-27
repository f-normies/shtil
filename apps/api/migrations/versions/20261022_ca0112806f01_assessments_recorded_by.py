"""assessments: врач, внёсший результат шкалы (#94).

Только добавляет колонку: у прежних строк NULL, их внёс пациент.

Revision ID: ca0112806f01
Revises: d6920e66414f
Create Date: 2026-10-22 02:13:30.518346

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ca0112806f01"
down_revision: str | Sequence[str] | None = "d6920e66414f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "results", sa.Column("recorded_by", sa.Uuid(), nullable=True), schema="assessments"
    )
    op.create_foreign_key(
        op.f("fk_results_recorded_by_users"),
        "results",
        "users",
        ["recorded_by"],
        ["id"],
        source_schema="assessments",
        referent_schema="identity",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("fk_results_recorded_by_users"), "results", schema="assessments", type_="foreignkey"
    )
    op.drop_column("results", "recorded_by", schema="assessments")
