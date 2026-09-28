from dataclasses import replace
from datetime import UTC, datetime

import pytest

from labjarvis.domain.models import (
    DomainError,
    LabSession,
    SessionState,
    SessionStep,
    StepState,
)
from labjarvis.domain.state_machine import (
    activate_step,
    complete_active_step,
    transition_session,
    transition_step,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def test_persisted_step_state_vocabulary_has_no_orthogonal_waiting_states() -> None:
    assert {state.value for state in StepState} == {
        "NOT_STARTED",
        "IN_PROGRESS",
        "COMPLETED",
        "SKIPPED_WITH_REASON",
    }


def make_session(*, protocol_approved: bool = True) -> LabSession:
    return LabSession(
        id="session-1",
        protocol_version_id="protocol-version-1",
        protocol_approved=protocol_approved,
        state=SessionState.READY,
        steps=(
            SessionStep(step_id="step-1", state=StepState.NOT_STARTED),
            SessionStep(step_id="step-2", state=StepState.NOT_STARTED),
        ),
        active_step_id=None,
        version=3,
        created_at=NOW,
    )


def test_session_cannot_start_with_unapproved_protocol() -> None:
    session = make_session(protocol_approved=False)

    with pytest.raises(DomainError, match="approved protocol"):
        transition_session(session, SessionState.ACTIVE, at=NOW)


def test_invalid_session_transition_is_rejected_without_mutating_input() -> None:
    session = make_session()

    with pytest.raises(DomainError, match=r"READY.*COMPLETED"):
        transition_session(session, SessionState.COMPLETED, at=NOW)

    assert session.state is SessionState.READY
    assert session.version == 3


def test_start_activates_first_step_and_increments_version() -> None:
    session = transition_session(make_session(), SessionState.ACTIVE, at=NOW)

    assert session.state is SessionState.ACTIVE
    assert session.active_step_id == "step-1"
    assert session.steps[0].state is StepState.IN_PROGRESS
    assert session.version == 4


def test_only_one_step_can_be_active() -> None:
    active = transition_session(make_session(), SessionState.ACTIVE, at=NOW)

    with pytest.raises(DomainError, match="already active"):
        activate_step(active, "step-2", at=NOW)


def test_completed_step_cannot_return_to_in_progress() -> None:
    step = replace(make_session().steps[0], state=StepState.COMPLETED)

    with pytest.raises(DomainError, match=r"COMPLETED.*IN_PROGRESS"):
        transition_step(step, StepState.IN_PROGRESS, at=NOW)


def test_completing_active_step_advances_once_to_next_step() -> None:
    active = transition_session(make_session(), SessionState.ACTIVE, at=NOW)

    advanced = complete_active_step(active, at=NOW)

    assert advanced.steps[0].state is StepState.COMPLETED
    assert advanced.steps[1].state is StepState.IN_PROGRESS
    assert advanced.active_step_id == "step-2"
    assert advanced.version == active.version + 1


def test_completing_final_step_completes_session() -> None:
    active = transition_session(make_session(), SessionState.ACTIVE, at=NOW)
    final_active = complete_active_step(active, at=NOW)

    completed = complete_active_step(final_active, at=NOW)

    assert completed.state is SessionState.COMPLETED
    assert completed.active_step_id is None
    assert completed.ended_at == NOW
    assert completed.version == final_active.version + 1
