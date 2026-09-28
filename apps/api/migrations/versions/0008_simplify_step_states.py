"""Map historical waiting step states to the executable IN_PROGRESS state."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0008_simplify_step_states"
down_revision = "0007_state_and_reagent_integrity"
branch_labels = None
depends_on = None

LEGACY_WAITING_STATES = {
    "AWAITING_INPUT",
    "AWAITING_REAGENT_VERIFICATION",
    "AWAITING_CONFIRMATION",
}


def _migrate_steps(raw: str) -> tuple[str, bool]:
    steps: list[dict[str, Any]] = json.loads(raw)
    changed = False
    for step in steps:
        if step.get("state") in LEGACY_WAITING_STATES:
            step["state"] = "IN_PROGRESS"
            changed = True
    return json.dumps(steps, separators=(",", ":"), sort_keys=True), changed


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, steps_json FROM sessions")).mappings()
    for row in rows:
        migrated, changed = _migrate_steps(row["steps_json"])
        if changed:
            connection.execute(
                sa.text("UPDATE sessions SET steps_json = :steps WHERE id = :id"),
                {"id": row["id"], "steps": migrated},
            )


def downgrade() -> None:
    # The three legacy values represented orthogonal UI waiting conditions, not
    # distinct scientific states. Their original spelling cannot be inferred
    # after canonicalization, and recreating one would fabricate history.
    pass
