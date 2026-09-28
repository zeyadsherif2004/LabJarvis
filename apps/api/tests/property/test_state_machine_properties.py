"""Direct properties of the pure session/step transition functions.

The service-level machine only reaches states the application can produce.
These properties also feed arbitrary, including inconsistent, sessions to every
transition so each guard is exercised: a transition either returns a session
that satisfies the invariants or raises ``DomainError`` - it never returns a
corrupt session and never mutates its input.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from labjarvis.domain.models import DomainError, LabSession, SessionState, SessionStep, StepState
from labjarvis.domain.state_machine import (
    ALLOWED_SESSION_TRANSITIONS,
    activate_step,
    complete_active_step,
    skip_active_step,
    transition_session,
    transition_step,
)

AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@st.composite
def sessions(draw: st.DrawFn) -> LabSession:
    size = draw(st.integers(min_value=0, max_value=5))
    steps = tuple(
        SessionStep(
            step_id=f"s{index}",
            state=draw(st.sampled_from(list(StepState))),
            skipped_reason=draw(st.sampled_from([None, "", "reason"])),
        )
        for index in range(size)
    )
    active = draw(st.sampled_from([None, "missing", *[step.step_id for step in steps]]))
    return LabSession(
        id="session",
        protocol_version_id="version",
        protocol_approved=draw(st.booleans()),
        state=draw(st.sampled_from(list(SessionState))),
        steps=steps,
        active_step_id=active,
        version=draw(st.integers(min_value=0, max_value=50)),
        created_at=AT,
    )


def _consistent(session: LabSession) -> None:
    in_progress = [step.step_id for step in session.steps if step.state is StepState.IN_PROGRESS]
    assert len(in_progress) <= 1
    if session.active_step_id is not None:
        assert in_progress == [session.active_step_id]


@given(sessions(), st.sampled_from(list(SessionState)))
def test_session_transitions_are_legal_or_refused(
    session: LabSession, target: SessionState
) -> None:
    try:
        result = transition_session(session, target, at=AT)
    except DomainError:
        return
    assert target in ALLOWED_SESSION_TRANSITIONS[session.state]
    assert result.version == session.version + 1
    assert result.state is target
    if target in {SessionState.COMPLETED, SessionState.ABORTED}:
        assert result.ended_at == AT


@given(sessions())
def test_terminal_sessions_accept_no_transition(session: LabSession) -> None:
    if session.state not in {SessionState.COMPLETED, SessionState.ABORTED}:
        return
    for target in SessionState:
        try:
            transition_session(session, target, at=AT)
        except DomainError:
            continue
        raise AssertionError(f"terminal {session.state} moved to {target}")


@given(sessions())
def test_completion_advances_exactly_once_or_refuses(session: LabSession) -> None:
    try:
        result = complete_active_step(session, at=AT)
    except DomainError:
        return
    assert session.state is SessionState.ACTIVE
    assert result.version == session.version + 1
    finished = next(s for s in result.steps if s.step_id == session.active_step_id)
    assert finished.state is StepState.COMPLETED
    if result.state is SessionState.ACTIVE:
        _consistent(result)
        assert result.active_step_id != session.active_step_id
    else:
        assert result.state is SessionState.COMPLETED
        assert result.active_step_id is None


@given(sessions(), st.sampled_from(["", "  ", "reagent unavailable"]))
def test_skip_records_the_reason_and_never_completes_the_step(
    session: LabSession, reason: str
) -> None:
    try:
        result = skip_active_step(session, reason=reason, at=AT)
    except DomainError:
        return
    assert reason.strip()
    skipped = next(s for s in result.steps if s.step_id == session.active_step_id)
    assert skipped.state is StepState.SKIPPED_WITH_REASON
    assert skipped.completed_at is None
    assert skipped.skipped_reason == reason.strip()
    assert result.version == session.version + 1


@given(sessions(), st.sampled_from(["s0", "s1", "s4", "missing"]))
def test_activation_preserves_a_single_active_step(session: LabSession, step_id: str) -> None:
    try:
        result = activate_step(session, step_id, at=AT)
    except DomainError:
        return
    assert session.active_step_id is None
    assert result.active_step_id == step_id


@given(
    st.sampled_from(list(StepState)),
    st.sampled_from(list(StepState)),
    st.sampled_from([None, "", "r"]),
)
def test_step_transitions_follow_the_table(
    source: StepState, target: StepState, reason: str | None
) -> None:
    step = SessionStep(step_id="s", state=source, skipped_reason=reason)
    try:
        result = transition_step(step, target, at=AT)
    except DomainError:
        return
    assert result.state is target
    assert step.state is source  # input untouched
    if target is StepState.SKIPPED_WITH_REASON:
        assert reason
