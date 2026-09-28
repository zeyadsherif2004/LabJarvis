"""Enforce singular pending actions and reagent reviews without deleting history."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from labjarvis.infrastructure.migration_support import index_exists

revision = "0007_state_and_reagent_integrity"
down_revision = "0006_spoken_confirmations"
branch_labels = None
depends_on = None

PENDING_ACTION_INDEX = "uq_pending_actions_one_pending_per_session"
PENDING_ACTION_READ_INDEX = "ix_pending_actions_session_status_created"
PENDING_VERIFICATION_INDEX = "uq_reagent_verifications_one_pending_per_requirement"
VERIFICATION_READ_INDEX = "ix_reagent_verifications_session_step_requirement_decision"
INTERACTIVE_SESSION_STATES = {"READY", "ACTIVE", "PAUSED"}


def _timestamp() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _reconcile_pending_actions(connection: Any) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT a.id, a.session_id, a.expected_session_version, a.created_at, "
            "s.version, s.state FROM pending_actions a "
            "JOIN sessions s ON s.id = a.session_id "
            "WHERE a.status = 'PENDING' "
            "ORDER BY a.session_id, a.created_at DESC, a.id DESC"
        )
    ).mappings()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row["session_id"]].append(row)

    expired_at = _timestamp()
    for candidates in grouped.values():
        retained = False
        for row in candidates:
            legal = (
                not retained
                and row["expected_session_version"] == row["version"]
                and row["state"] in INTERACTIVE_SESSION_STATES
            )
            if legal:
                retained = True
                continue
            connection.execute(
                sa.text(
                    "UPDATE pending_actions SET status = 'EXPIRED', resolved_at = :resolved_at "
                    "WHERE id = :id AND status = 'PENDING'"
                ),
                {"id": row["id"], "resolved_at": expired_at},
            )


def _active_step_ids(steps_json: str) -> set[str]:
    try:
        steps = json.loads(steps_json)
    except (TypeError, ValueError):
        return set()
    return {
        str(step.get("step_id", step.get("id", "")))
        for step in steps
        if step.get("state") == "IN_PROGRESS"
    }


def _reconcile_pending_verifications(connection: Any) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT v.id, v.session_id, v.step_id, v.requirement_id, v.created_at, "
            "s.state, s.active_step_id, s.steps_json FROM reagent_verifications v "
            "JOIN sessions s ON s.id = v.session_id "
            "WHERE v.decision = 'PENDING' "
            "ORDER BY v.session_id, v.step_id, v.requirement_id, v.created_at DESC, v.id DESC"
        )
    ).mappings()
    grouped: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for row in rows:
        grouped[(row["session_id"], row["step_id"], row["requirement_id"])].append(row)

    stale_at = _timestamp()
    for candidates in grouped.values():
        retained = False
        for row in candidates:
            active_steps = _active_step_ids(row["steps_json"])
            legal = (
                not retained
                and row["state"] == "ACTIVE"
                and row["active_step_id"] == row["step_id"]
                and row["step_id"] in active_steps
            )
            if legal:
                retained = True
                continue
            connection.execute(
                sa.text(
                    "UPDATE reagent_verifications SET decision = 'STALE', "
                    "resolved_at = :resolved_at WHERE id = :id AND decision = 'PENDING'"
                ),
                {"id": row["id"], "resolved_at": stale_at},
            )


def upgrade() -> None:
    connection = op.get_bind()
    _reconcile_pending_actions(connection)
    _reconcile_pending_verifications(connection)

    if not index_exists(connection, "pending_actions", PENDING_ACTION_INDEX):
        op.create_index(
            PENDING_ACTION_INDEX,
            "pending_actions",
            ["session_id"],
            unique=True,
            sqlite_where=sa.text("status = 'PENDING'"),
            postgresql_where=sa.text("status = 'PENDING'"),
        )
    if not index_exists(connection, "pending_actions", PENDING_ACTION_READ_INDEX):
        op.create_index(
            PENDING_ACTION_READ_INDEX,
            "pending_actions",
            ["session_id", "status", "created_at"],
        )
    if not index_exists(connection, "reagent_verifications", PENDING_VERIFICATION_INDEX):
        op.create_index(
            PENDING_VERIFICATION_INDEX,
            "reagent_verifications",
            ["session_id", "step_id", "requirement_id"],
            unique=True,
            sqlite_where=sa.text("decision = 'PENDING'"),
            postgresql_where=sa.text("decision = 'PENDING'"),
        )
    if not index_exists(connection, "reagent_verifications", VERIFICATION_READ_INDEX):
        op.create_index(
            VERIFICATION_READ_INDEX,
            "reagent_verifications",
            ["session_id", "step_id", "requirement_id", "decision"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table, name in (
        ("reagent_verifications", VERIFICATION_READ_INDEX),
        ("reagent_verifications", PENDING_VERIFICATION_INDEX),
        ("pending_actions", PENDING_ACTION_READ_INDEX),
        ("pending_actions", PENDING_ACTION_INDEX),
    ):
        if index_exists(connection, table, name):
            op.drop_index(name, table_name=table)
    connection.execute(
        sa.text("UPDATE pending_actions SET status = 'REJECTED' WHERE status = 'EXPIRED'")
    )
    connection.execute(
        sa.text(
            "UPDATE reagent_verifications SET decision = 'REJECTED' WHERE decision = 'STALE'"
        )
    )
