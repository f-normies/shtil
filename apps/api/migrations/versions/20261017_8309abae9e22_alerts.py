"""alerts

Revision ID: 8309abae9e22
Revises: 6b0540453386
Create Date: 2026-10-17 17:37:41.955962

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8309abae9e22"
down_revision: str | Sequence[str] | None = "6b0540453386"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS alerts")
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("origin_type", sa.String(length=16), nullable=False),
        sa.Column("rule_id", sa.String(length=200), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("doctor_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_via", sa.String(length=8), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(origin_type = 'rule') = (jsonb_array_length(sources) >= 1)",
            name=op.f("ck_alerts_sources_match_origin"),
        ),
        sa.CheckConstraint(
            "(origin_type = 'rule') = (rule_id IS NOT NULL) "
            "AND (rule_id IS NULL) = (rule_version IS NULL)",
            name=op.f("ck_alerts_rule_matches_origin"),
        ),
        sa.CheckConstraint("delivered_via IN ('ws', 'rest')", name=op.f("ck_alerts_delivered_via")),
        sa.CheckConstraint("kind IN ('alert', 'routing')", name=op.f("ck_alerts_kind")),
        sa.CheckConstraint("level IN ('mandatory', 'desirable')", name=op.f("ck_alerts_level")),
        sa.CheckConstraint(
            "origin_type = 'rule' OR kind = 'alert'", name=op.f("ck_alerts_ai_kind")
        ),
        sa.CheckConstraint("origin_type IN ('rule', 'ai_run')", name=op.f("ck_alerts_origin_type")),
        sa.CheckConstraint(
            "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)",
            name=op.f("ck_alerts_acknowledge_complete"),
        ),
        sa.CheckConstraint(
            "(delivered_at IS NULL) = (delivered_via IS NULL)",
            name=op.f("ck_alerts_delivery_complete"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(doctor_actions) >= 1", name=op.f("ck_alerts_doctor_actions")
        ),
        sa.CheckConstraint("occurrences >= 1", name=op.f("ck_alerts_occurrences")),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["identity.users.id"], name=op.f("fk_alerts_acknowledged_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"], ["identity.users.id"], name=op.f("fk_alerts_doctor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["identity.users.id"], name=op.f("fk_alerts_patient_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        schema="alerts",
    )
    op.create_index(
        op.f("ix_alerts_alerts_doctor_id"), "alerts", ["doctor_id"], unique=False, schema="alerts"
    )
    op.create_index(
        op.f("ix_alerts_alerts_patient_id"), "alerts", ["patient_id"], unique=False, schema="alerts"
    )
    op.create_index(
        "uq_alerts_open_rule",
        "alerts",
        ["patient_id", "rule_id"],
        unique=True,
        schema="alerts",
        postgresql_where=sa.text("acknowledged_at IS NULL AND rule_id IS NOT NULL"),
    )
    op.create_table(
        "triggers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("origin_ref", sa.String(length=200), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("level IN ('mandatory', 'desirable')", name=op.f("ck_triggers_level")),
        sa.CheckConstraint("origin_ref <> ''", name=op.f("ck_triggers_origin_ref")),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.alerts.id"], name=op.f("fk_triggers_alert_id_alerts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triggers")),
        schema="alerts",
    )
    op.create_index(
        op.f("ix_alerts_triggers_alert_id"), "triggers", ["alert_id"], unique=False, schema="alerts"
    )

    # Медицинские данные физически не удаляются. История срабатываний только дописывается:
    # UPDATE, DELETE и TRUNCATE запрещены. Сигнал можно изменить: доставка, подтверждение,
    # подъём уровня. Удалить его нельзя. Тесты очищают таблицы с
    # session_replication_role = replica.
    op.execute("""
        CREATE FUNCTION alerts.forbid_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '%.% keeps medical data: % is forbidden',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER triggers_append_only
        BEFORE UPDATE OR DELETE ON alerts.triggers
        FOR EACH ROW EXECUTE FUNCTION alerts.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER triggers_no_truncate
        BEFORE TRUNCATE ON alerts.triggers
        FOR EACH STATEMENT EXECUTE FUNCTION alerts.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER alerts_no_delete
        BEFORE DELETE ON alerts.alerts
        FOR EACH ROW EXECUTE FUNCTION alerts.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER alerts_no_truncate
        BEFORE TRUNCATE ON alerts.alerts
        FOR EACH STATEMENT EXECUTE FUNCTION alerts.forbid_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER alerts_no_truncate ON alerts.alerts")
    op.execute("DROP TRIGGER alerts_no_delete ON alerts.alerts")
    op.execute("DROP TRIGGER triggers_no_truncate ON alerts.triggers")
    op.execute("DROP TRIGGER triggers_append_only ON alerts.triggers")
    op.execute("DROP FUNCTION alerts.forbid_change()")
    op.drop_index(op.f("ix_alerts_triggers_alert_id"), table_name="triggers", schema="alerts")
    op.drop_table("triggers", schema="alerts")
    op.drop_index(
        "uq_alerts_open_rule",
        table_name="alerts",
        schema="alerts",
        postgresql_where=sa.text("acknowledged_at IS NULL AND rule_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_alerts_alerts_patient_id"), table_name="alerts", schema="alerts")
    op.drop_index(op.f("ix_alerts_alerts_doctor_id"), table_name="alerts", schema="alerts")
    op.drop_table("alerts", schema="alerts")
    op.execute("DROP SCHEMA alerts")
