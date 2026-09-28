"""Pure timer transitions derived from stored wall-clock timestamps.

A laboratory timer outlives the browser tab that started it. Remaining time is
therefore never held as a counter: it is always recomputed from an absolute
deadline (while running) or from the remainder frozen at the moment of a pause.
That makes a timer identical before and after a reload or a backend restart.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timedelta

from labjarvis.domain.models import DomainError, SessionTimer, TimerState

MAX_TIMER_SECONDS = 604800
TERMINAL_STATES = frozenset({TimerState.ELAPSED, TimerState.CANCELLED})


def _require_aware(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise DomainError("timer timestamps must be timezone-aware")
    return at


def start_timer(
    *,
    timer_id: str,
    session_id: str,
    step_id: str | None,
    label: str,
    duration_seconds: int,
    at: datetime,
    correlation_id: str,
) -> SessionTimer:
    """Create a running timer with an absolute deadline."""

    _require_aware(at)
    if duration_seconds < 1 or duration_seconds > MAX_TIMER_SECONDS:
        raise DomainError("timer duration must be between one second and seven days")
    if not label.strip():
        raise DomainError("a timer requires a label")
    if not correlation_id.strip():
        raise DomainError("a timer requires a correlation identifier")
    return SessionTimer(
        id=timer_id,
        session_id=session_id,
        step_id=step_id,
        label=" ".join(label.split()),
        duration_seconds=duration_seconds,
        state=TimerState.RUNNING,
        created_at=at,
        started_at=at,
        deadline_at=at + timedelta(seconds=duration_seconds),
        remaining_seconds_at_pause=None,
        paused_at=None,
        resolved_at=None,
        correlation_id=correlation_id,
        version=0,
    )


def remaining_seconds(timer: SessionTimer, *, now: datetime) -> int:
    """Return whole seconds left, rounded up so a live timer never reads zero early."""

    _require_aware(now)
    if timer.state in TERMINAL_STATES:
        return 0
    if timer.state is TimerState.PAUSED:
        return timer.remaining_seconds_at_pause or 0
    if timer.deadline_at is None:  # pragma: no cover - running timers always carry a deadline
        raise DomainError("a running timer must carry a deadline")
    return max(0, math.ceil((timer.deadline_at - now).total_seconds()))


def pause_timer(timer: SessionTimer, *, at: datetime) -> SessionTimer:
    """Freeze the remainder so elapsed wall-clock time stops consuming the timer."""

    _require_aware(at)
    if timer.state is not TimerState.RUNNING:
        raise DomainError(f"a {timer.state.value} timer cannot be paused")
    return replace(
        timer,
        state=TimerState.PAUSED,
        deadline_at=None,
        remaining_seconds_at_pause=remaining_seconds(timer, now=at),
        paused_at=at,
        version=timer.version + 1,
    )


def resume_timer(timer: SessionTimer, *, at: datetime) -> SessionTimer:
    """Rebase the deadline on the resume moment, preserving the frozen remainder."""

    _require_aware(at)
    if timer.state is not TimerState.PAUSED:
        raise DomainError(f"a {timer.state.value} timer cannot be resumed")
    remainder = timer.remaining_seconds_at_pause or 0
    return replace(
        timer,
        state=TimerState.RUNNING,
        deadline_at=at + timedelta(seconds=remainder),
        remaining_seconds_at_pause=None,
        paused_at=None,
        version=timer.version + 1,
    )


def cancel_timer(timer: SessionTimer, *, at: datetime) -> SessionTimer:
    """Retire a timer without claiming that its interval completed."""

    _require_aware(at)
    if timer.state in TERMINAL_STATES:
        raise DomainError(f"a {timer.state.value} timer cannot be cancelled")
    return replace(
        timer,
        state=TimerState.CANCELLED,
        deadline_at=None,
        remaining_seconds_at_pause=None,
        resolved_at=at,
        version=timer.version + 1,
    )


def settle_timer(timer: SessionTimer, *, at: datetime) -> SessionTimer | None:
    """Return an elapsed timer, or ``None`` when nothing has changed.

    Elapse is discovered lazily on read rather than by a background scheduler, so
    a timer that expired while the process was stopped still settles correctly on
    the next request. Reaching the deadline never advances protocol state; it
    only records that the interval passed.
    """

    _require_aware(at)
    if timer.state is not TimerState.RUNNING:
        return None
    if remaining_seconds(timer, now=at) > 0:
        return None
    return replace(
        timer,
        state=TimerState.ELAPSED,
        deadline_at=timer.deadline_at,
        resolved_at=at,
        version=timer.version + 1,
    )
