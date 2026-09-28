"""Historical waiting states remain readable during rolling migration."""

from __future__ import annotations

import json

import pytest

from labjarvis.domain.models import StepState
from labjarvis.infrastructure.database import _session_steps


@pytest.mark.parametrize(
    "legacy",
    ["AWAITING_INPUT", "AWAITING_REAGENT_VERIFICATION", "AWAITING_CONFIRMATION"],
)
def test_historical_waiting_step_deserializes_as_in_progress(legacy: str) -> None:
    serialised = json.dumps(
        [
            {
                "step_id": "step-1",
                "state": legacy,
                "started_at": None,
                "completed_at": None,
                "skipped_reason": None,
            }
        ]
    )

    assert _session_steps(serialised)[0].state is StepState.IN_PROGRESS
