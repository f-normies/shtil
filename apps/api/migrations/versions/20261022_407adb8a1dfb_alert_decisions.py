"""alert decisions: state, snooze, resolution and actions

Revision ID: 407adb8a1dfb
Revises: ed8b56d79336
Create Date: 2026-10-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "407adb8a1dfb"
down_revision: str | Sequence[str] | None = "ed8b56d79336"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "alerts",
        sa.Column("state", sa.String(length=16), server_default="new", nullable=False),
        schema="alerts",
    )
    op.add_column("alerts", sa.Column("snoozed_until", sa.Date(), nullable=True), schema="alerts")
    op.add_column(
        "alerts",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema="alerts",
    )
    op.add_column("alerts", sa.Column("resolved_by", sa.Uuid(), nullable=True), schema="alerts")
    op.create_foreign_key(
        op.f("fk_alerts_resolved_by_users"),
        "alerts",
        "users",
        ["resolved_by"],
        ["id"],
        source_schema="alerts",
        referent_schema="identity",
    )
    # Подтверждённый до #95 сигнал — «в работе»: решения по нему ещё нет.
    op.execute("UPDATE alerts.alerts SET state = 'in_work' WHERE acknowledged_at IS NOT NULL")
    op.create_table(
        "actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=True),
        sa.Column("decision_text", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snoozed_until", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "action IN ('acknowledge', 'snooze', 'unsnooze', 'no_contact', 'resolve')",
            name=op.f("ck_actions_action"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["identity.users.id"], name=op.f("fk_actions_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.alerts.id"], name=op.f("fk_actions_alert_id_alerts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actions")),
        schema="alerts",
    )
    op.create_index(
        op.f("ix_alerts_actions_alert_id"), "actions", ["alert_id"], unique=False, schema="alerts"
    )
    # До #95 новое срабатывание после подтверждения создавало второй сигнал по правилу.
    # Подтверждённый сигнал, за которым по тому же правилу есть более поздний, врач уже
    # закрыл подтверждением: он решён, в истории — запись от того же врача в то же время.
    # Иначе уникальный индекс «один нерешённый сигнал на правило» не создаётся.
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY patient_id, rule_id ORDER BY created_at DESC, id DESC
            ) AS n
            FROM alerts.alerts
            WHERE rule_id IS NOT NULL
        ),
        closed AS (
            UPDATE alerts.alerts a
            SET state = 'resolved', resolved_at = a.acknowledged_at,
                resolved_by = a.acknowledged_by
            FROM ranked r
            WHERE a.id = r.id AND r.n > 1 AND a.acknowledged_at IS NOT NULL
            RETURNING a.id, a.acknowledged_at, a.acknowledged_by
        )
        INSERT INTO alerts.actions (alert_id, at, actor_id, action, reason)
        SELECT id, acknowledged_at, acknowledged_by, 'resolve',
            'По этому правилу есть более поздний сигнал'
        FROM closed
    """)
    op.create_check_constraint(
        op.f("ck_alerts_state"),
        "alerts",
        "state IN ('new', 'in_work', 'snoozed', 'resolved')",
        schema="alerts",
    )
    op.create_check_constraint(
        op.f("ck_alerts_snooze_date"),
        "alerts",
        "(state = 'snoozed') = (snoozed_until IS NOT NULL)",
        schema="alerts",
    )
    op.create_check_constraint(
        op.f("ck_alerts_resolve_complete"),
        "alerts",
        "(state = 'resolved') = (resolved_at IS NOT NULL) "
        "AND (resolved_at IS NULL) = (resolved_by IS NULL)",
        schema="alerts",
    )
    op.drop_index("uq_alerts_open_rule", table_name="alerts", schema="alerts")
    op.create_index(
        "uq_alerts_open_rule",
        "alerts",
        ["patient_id", "rule_id"],
        unique=True,
        schema="alerts",
        postgresql_where=sa.text("state <> 'resolved' AND rule_id IS NOT NULL"),
    )

    # История действий только дописывается, как история срабатываний (#12).
    op.execute("""
        CREATE TRIGGER actions_append_only
        BEFORE UPDATE OR DELETE ON alerts.actions
        FOR EACH ROW EXECUTE FUNCTION alerts.forbid_change()
    """)
    op.execute("""
        CREATE TRIGGER actions_no_truncate
        BEFORE TRUNCATE ON alerts.actions
        FOR EACH STATEMENT EXECUTE FUNCTION alerts.forbid_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER actions_no_truncate ON alerts.actions")
    op.execute("DROP TRIGGER actions_append_only ON alerts.actions")
    op.drop_index(op.f("ix_alerts_actions_alert_id"), table_name="actions", schema="alerts")
    op.drop_table("actions", schema="alerts")
    op.drop_index("uq_alerts_open_rule", table_name="alerts", schema="alerts")
    op.create_index(
        "uq_alerts_open_rule",
        "alerts",
        ["patient_id", "rule_id"],
        unique=True,
        schema="alerts",
        postgresql_where=sa.text("acknowledged_at IS NULL AND rule_id IS NOT NULL"),
    )
    for name in ("resolve_complete", "snooze_date", "state"):
        op.drop_constraint(op.f(f"ck_alerts_{name}"), "alerts", schema="alerts", type_="check")
    op.drop_constraint(
        op.f("fk_alerts_resolved_by_users"), "alerts", schema="alerts", type_="foreignkey"
    )
    for column in ("resolved_by", "resolved_at", "snoozed_until", "state"):
        op.drop_column("alerts", column, schema="alerts")
