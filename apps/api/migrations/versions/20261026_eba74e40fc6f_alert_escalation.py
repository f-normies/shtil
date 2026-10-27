"""alert escalation: urgent_since, deputies, reviews and deputy access grants

Revision ID: eba74e40fc6f
Revises: 3b7e1c9d4a20
Create Date: 2026-10-26 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eba74e40fc6f"
down_revision: str | Sequence[str] | None = "3b7e1c9d4a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = "action IN ('acknowledge', 'snooze', 'unsnooze', 'no_contact', 'resolve')"
NEW_ACTIONS = "action IN ('acknowledge', 'snooze', 'unsnooze', 'no_contact', 'resolve', 'escalate')"


def upgrade() -> None:
    """Upgrade schema."""
    # Сигналы до эскалации остаются без urgent_since: таймеров у них нет (спека #56, 9.4).
    op.add_column(
        "alerts",
        sa.Column("urgent_since", sa.DateTime(timezone=True), nullable=True),
        schema="alerts",
    )
    op.add_column("alerts", sa.Column("deputy_id", sa.Uuid(), nullable=True), schema="alerts")
    op.create_foreign_key(
        op.f("fk_alerts_deputy_id_users"),
        "alerts",
        "users",
        ["deputy_id"],
        ["id"],
        source_schema="alerts",
        referent_schema="identity",
    )

    op.add_column("actions", sa.Column("step", sa.SmallInteger(), nullable=True), schema="alerts")
    op.add_column("actions", sa.Column("recipient_id", sa.Uuid(), nullable=True), schema="alerts")
    op.create_foreign_key(
        op.f("fk_actions_recipient_id_users"),
        "actions",
        "users",
        ["recipient_id"],
        ["id"],
        source_schema="alerts",
        referent_schema="identity",
    )
    # Новое значение действия: прежние значения проходят и новое ограничение.
    op.drop_constraint(op.f("ck_actions_action"), "actions", schema="alerts", type_="check")
    op.create_check_constraint(op.f("ck_actions_action"), "actions", NEW_ACTIONS, schema="alerts")
    op.create_check_constraint(
        op.f("ck_actions_escalate_step"),
        "actions",
        "(action = 'escalate') = (step IS NOT NULL)",
        schema="alerts",
    )

    op.create_table(
        "deputies",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("deputy_id", sa.Uuid(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("doctor_id <> deputy_id", name=op.f("ck_deputies_not_self")),
        sa.CheckConstraint("starts_on <= ends_on", name=op.f("ck_deputies_period")),
        sa.CheckConstraint("ends_on - starts_on < 92", name=op.f("ck_deputies_period_length")),
        sa.CheckConstraint("btrim(basis) <> ''", name=op.f("ck_deputies_basis")),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by IS NULL)", name=op.f("ck_deputies_revoke_complete")
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["identity.users.id"], name=op.f("fk_deputies_doctor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["deputy_id"], ["identity.users.id"], name=op.f("fk_deputies_deputy_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["identity.users.id"], name=op.f("fk_deputies_assigned_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["identity.users.id"], name=op.f("fk_deputies_revoked_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deputies")),
        schema="alerts",
    )
    op.create_index(
        op.f("ix_alerts_deputies_doctor_id"), "deputies", ["doctor_id"], schema="alerts"
    )
    op.create_index(
        op.f("ix_alerts_deputies_deputy_id"), "deputies", ["deputy_id"], schema="alerts"
    )

    op.create_table(
        "reviews",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=False),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.alerts.id"], name=op.f("fk_reviews_alert_id_alerts")
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["identity.users.id"], name=op.f("fk_reviews_reviewed_by_users")
        ),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_reviews")),
        schema="alerts",
    )

    op.create_table(
        "access_grants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "end_reason IN ('resolved', 'lead_took', 'revoked')",
            name=op.f("ck_access_grants_end_reason"),
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (end_reason IS NULL)", name=op.f("ck_access_grants_end_complete")
        ),
        sa.CheckConstraint("expires_at > granted_at", name=op.f("ck_access_grants_expires")),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patients.user_id"],
            name=op.f("fk_access_grants_patient_id_patients"),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["identity.users.id"], name=op.f("fk_access_grants_doctor_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_grants")),
        schema="patients",
    )
    op.create_index(
        op.f("ix_patients_access_grants_alert_id"), "access_grants", ["alert_id"], schema="patients"
    )
    op.create_index(
        "ix_patients_access_grants_doctor_patient",
        "access_grants",
        ["doctor_id", "patient_id"],
        schema="patients",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_patients_access_grants_doctor_patient", table_name="access_grants", schema="patients"
    )
    op.drop_index(
        op.f("ix_patients_access_grants_alert_id"), table_name="access_grants", schema="patients"
    )
    op.drop_table("access_grants", schema="patients")
    op.drop_table("reviews", schema="alerts")
    op.drop_index(op.f("ix_alerts_deputies_deputy_id"), table_name="deputies", schema="alerts")
    op.drop_index(op.f("ix_alerts_deputies_doctor_id"), table_name="deputies", schema="alerts")
    op.drop_table("deputies", schema="alerts")
    op.drop_constraint(op.f("ck_actions_escalate_step"), "actions", schema="alerts", type_="check")
    op.drop_constraint(op.f("ck_actions_action"), "actions", schema="alerts", type_="check")
    op.create_check_constraint(op.f("ck_actions_action"), "actions", OLD_ACTIONS, schema="alerts")
    op.drop_constraint(
        op.f("fk_actions_recipient_id_users"), "actions", schema="alerts", type_="foreignkey"
    )
    op.drop_column("actions", "recipient_id", schema="alerts")
    op.drop_column("actions", "step", schema="alerts")
    op.drop_constraint(
        op.f("fk_alerts_deputy_id_users"), "alerts", schema="alerts", type_="foreignkey"
    )
    op.drop_column("alerts", "deputy_id", schema="alerts")
    op.drop_column("alerts", "urgent_since", schema="alerts")
