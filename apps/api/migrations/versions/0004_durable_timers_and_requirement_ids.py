"""Persist laboratory timers and give every reagent requirement a stable identifier.

Timers previously lived only in browser memory, so a reload lost them. They now
have a durable home whose remaining time is derived from stored timestamps.

Requirements gain ``requirement_id`` so a step with more than one reagent can be
verified per reagent instead of only at position zero. Existing rows are
backfilled positionally, which reproduces the identifiers a fresh extraction
would assign, so already-approved protocol versions keep working unchanged.
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

from labjarvis.infrastructure.migration_support import (
    adopt_existing_table,
    column_exists,
    index_exists,
)

revision = "0004_durable_timers_and_requirement_ids"
down_revision = "0003_verification_review"
branch_labels = None
depends_on = None


def _backfill_requirement_ids(*, add: bool) -> None:
    """Rewrite ``protocol_versions.steps_json`` in place, adding or removing ids."""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, steps_json FROM protocol_versions")
    ).fetchall()
    for version_id, steps_json in rows:
        try:
            steps = json.loads(steps_json)
        except (TypeError, ValueError):  # pragma: no cover - defensive on corrupt rows
            continue
        changed = False
        for step in steps:
            step_id = step.get("id", "step")
            for position, requirement in enumerate(step.get("requirements", []), start=1):
                if add:
                    if "requirement_id" not in requirement:
                        requirement["requirement_id"] = f"{step_id}-req-{position}"
                        changed = True
                elif requirement.pop("requirement_id", None) is not None:
                    changed = True
        if changed:
            connection.execute(
                sa.text("UPDATE protocol_versions SET steps_json = :steps WHERE id = :id"),
                {
                    "steps": json.dumps(steps, separators=(",", ":"), sort_keys=True),
                    "id": version_id,
                },
            )


SESSION_TIMER_COLUMNS = {
    "id",
    "session_id",
    "step_id",
    "label",
    "duration_seconds",
    "state",
    "created_at",
    "started_at",
    "deadline_at",
    "remaining_seconds_at_pause",
    "paused_at",
    "resolved_at",
    "correlation_id",
    "version",
}


def _widen_alembic_version_column(connection: Connection) -> None:
    """Allow this and later descriptive revision identifiers on PostgreSQL.

    Alembic creates ``alembic_version.version_num`` as ``VARCHAR(32)`` by
    default. SQLite does not enforce that width, which hid the fact that this
    revision identifier is longer until a real PostgreSQL migration ran.
    Widen before Alembic records this revision at the end of ``upgrade``.
    """

    if connection.dialect.name != "postgresql":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def upgrade() -> None:
    connection = op.get_bind()
    _widen_alembic_version_column(connection)

    # Runtime schema creation may already have produced this table against a
    # database stamped at 0003. Adopting a matching table is safe; adopting one
    # that merely shares the name is not, and raises.
    if adopt_existing_table(
        connection,
        "session_timers",
        required_columns=SESSION_TIMER_COLUMNS,
        revision=revision,
    ):
        op.create_table(
            "session_timers",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(length=36),
                sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("step_id", sa.String(length=80), nullable=True),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("duration_seconds", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("remaining_seconds_at_pause", sa.Integer(), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("correlation_id", sa.String(length=80), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
        )
    if not index_exists(connection, "session_timers", "ix_session_timers_session_state"):
        op.create_index(
            "ix_session_timers_session_state",
            "session_timers",
            ["session_id", "state"],
        )
    # Adopting a table suppresses only its CREATE. Everything the pre-creation
    # did *not* do - this column, and the JSON backfill below - still runs.
    if not column_exists(connection, "reagent_verifications", "requirement_id"):
        op.add_column(
            "reagent_verifications",
            sa.Column(
                "requirement_id", sa.String(length=120), nullable=False, server_default=""
            ),
        )
    _backfill_requirement_ids(add=True)


def downgrade() -> None:
    _backfill_requirement_ids(add=False)
    with op.batch_alter_table("reagent_verifications") as batch:
        batch.drop_column("requirement_id")
    op.drop_index("ix_session_timers_session_state", table_name="session_timers")
    op.drop_table("session_timers")
