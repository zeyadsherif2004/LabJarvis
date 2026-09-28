"""Pure state transitions for sessions and protocol steps."""

from dataclasses import replace
from datetime import datetime

from labjarvis.domain.models import (
    DomainError,
    LabSession,
    SessionState,
    SessionStep,
    StepState,
)

ALLOWED_SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.DRAFT: frozenset({SessionState.READY}),
    SessionState.READY: frozenset({SessionState.ACTIVE, SessionState.ABORTED}),
    SessionState.ACTIVE: frozenset(
        {SessionState.PAUSED, SessionState.COMPLETED, SessionState.ABORTED}
    ),
    SessionState.PAUSED: frozenset({SessionState.ACTIVE, SessionState.ABORTED}),
    SessionState.COMPLETED: frozenset(),
    SessionState.ABORTED: frozenset(),
}

ALLOWED_STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.NOT_STARTED: frozenset({StepState.IN_PROGRESS, StepState.SKIPPED_WITH_REASON}),
    StepState.IN_PROGRESS: frozenset(
        {
            StepState.COMPLETED,
            StepState.SKIPPED_WITH_REASON,
        }
    ),
    StepState.COMPLETED: frozenset(),
    StepState.SKIPPED_WITH_REASON: frozenset(),
}


def transition_step(step: SessionStep, target: StepState, *, at: datetime) -> SessionStep:
    """Return a transitioned step or fail without changing the input."""

    if target not in ALLOWED_STEP_TRANSITIONS[step.state]:
        raise DomainError(f"invalid step transition {step.state.value} -> {target.value}")
    if target is StepState.SKIPPED_WITH_REASON and not step.skipped_reason:
        raise DomainError("skipping a step requires a reason")
    return replace(
        step,
        state=target,
        started_at=at
        if target is StepState.IN_PROGRESS and step.started_at is None
        else step.started_at,
        completed_at=at if target is StepState.COMPLETED else step.completed_at,
    )


def _replace_step(session: LabSession, replacement: SessionStep) -> LabSession:
    return replace(
        session,
        steps=tuple(
            replacement if step.step_id == replacement.step_id else step for step in session.steps
        ),
    )


def activate_step(session: LabSession, step_id: str, *, at: datetime) -> LabSession:
    """Activate one not-started step while preserving the single-active-step invariant."""

    if session.state is not SessionState.ACTIVE:
        raise DomainError("a step can be activated only in an ACTIVE session")
    if session.active_step_id is not None:
        raise DomainError(f"step {session.active_step_id} is already active")
    try:
        step = next(item for item in session.steps if item.step_id == step_id)
    except StopIteration as exc:
        raise DomainError(f"unknown protocol step {step_id}") from exc
    activated = transition_step(step, StepState.IN_PROGRESS, at=at)
    return replace(_replace_step(session, activated), active_step_id=step_id)


def _require_single_in_progress(session: LabSession) -> None:
    """Refuse to advance a session whose steps are already inconsistent.

    Only the active step may be IN_PROGRESS. The application never produces
    anything else, but a corrupted or hand-edited record must fail closed
    rather than be advanced into a session with two steps in progress.
    """

    stray = [
        step.step_id
        for step in session.steps
        if step.state is StepState.IN_PROGRESS and step.step_id != session.active_step_id
    ]
    if stray:
        raise DomainError("session is inconsistent: a non-active step is in progress")


def complete_active_step(session: LabSession, *, at: datetime) -> LabSession:
    """Complete the active step and advance or finish with one version increment."""

    if session.state is not SessionState.ACTIVE:
        raise DomainError("a step can be completed only in an ACTIVE session")
    if session.active_step_id is None:
        raise DomainError("session has no active step to complete")
    active = next(
        (step for step in session.steps if step.step_id == session.active_step_id),
        None,
    )
    if active is None:
        raise DomainError("active step is missing from the session")
    _require_single_in_progress(session)

    completed_step = transition_step(active, StepState.COMPLETED, at=at)
    advanced = replace(
        _replace_step(session, completed_step),
        active_step_id=None,
        version=session.version + 1,
    )
    next_step = next(
        (step for step in advanced.steps if step.state is StepState.NOT_STARTED),
        None,
    )
    if next_step is None:
        return replace(advanced, state=SessionState.COMPLETED, ended_at=at)

    activated = transition_step(next_step, StepState.IN_PROGRESS, at=at)
    return replace(_replace_step(advanced, activated), active_step_id=activated.step_id)


def skip_active_step(session: LabSession, *, reason: str, at: datetime) -> LabSession:
    """Skip the active step with a recorded reason and advance, or finish.

    Skipping is deliberately shaped like completion: it advances the protocol and
    increments the session version exactly once. What differs is that the step is
    permanently marked ``SKIPPED_WITH_REASON`` and the reason is stored on it, so
    the notebook can never present a skipped step as a performed one.
    """

    if session.state is not SessionState.ACTIVE:
        raise DomainError("a step can be skipped only in an ACTIVE session")
    if session.active_step_id is None:
        raise DomainError("session has no active step to skip")
    if not reason.strip():
        raise DomainError("skipping a step requires a reason")
    _require_single_in_progress(session)
    active = next(
        (step for step in session.steps if step.step_id == session.active_step_id),
        None,
    )
    if active is None:
        raise DomainError("active step is missing from the session")

    skipped = transition_step(
        replace(active, skipped_reason=reason.strip()),
        StepState.SKIPPED_WITH_REASON,
        at=at,
    )
    advanced = replace(
        _replace_step(session, skipped),
        active_step_id=None,
        version=session.version + 1,
    )
    next_step = next(
        (step for step in advanced.steps if step.state is StepState.NOT_STARTED),
        None,
    )
    if next_step is None:
        return replace(advanced, state=SessionState.COMPLETED, ended_at=at)

    activated = transition_step(next_step, StepState.IN_PROGRESS, at=at)
    return replace(_replace_step(advanced, activated), active_step_id=activated.step_id)


def transition_session(
    session: LabSession,
    target: SessionState,
    *,
    at: datetime,
) -> LabSession:
    """Apply a legal session transition and increment its optimistic version."""

    if target not in ALLOWED_SESSION_TRANSITIONS[session.state]:
        raise DomainError(f"invalid session transition {session.state.value} -> {target.value}")
    if target is SessionState.ACTIVE and not session.protocol_approved:
        raise DomainError("an approved protocol version is required before starting")

    transitioned = replace(
        session,
        state=target,
        version=session.version + 1,
        started_at=at
        if target is SessionState.ACTIVE and session.started_at is None
        else session.started_at,
        ended_at=at
        if target in {SessionState.COMPLETED, SessionState.ABORTED}
        else session.ended_at,
    )
    if target is SessionState.ACTIVE and session.state is SessionState.READY:
        try:
            first_step = next(
                step for step in transitioned.steps if step.state is StepState.NOT_STARTED
            )
        except StopIteration as exc:
            raise DomainError("an active session requires at least one incomplete step") from exc
        transitioned = activate_step(transitioned, first_step.step_id, at=at)
    return transitioned
