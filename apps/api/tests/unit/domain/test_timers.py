"""Timers are deterministic wall-clock facts, not client-side intervals.

A timer must survive a browser reload and a backend restart, so its remaining
time is always derived from stored timestamps rather than from a counter that
only exists while a tab is open.
"""

from datetime import UTC, datetime, timedelta

import pytest

from labjarvis.domain.models import DomainError, SessionTimer, TimerState
from labjarvis.domain.timers import (
    cancel_timer,
    pause_timer,
    remaining_seconds,
    resume_timer,
    settle_timer,
    start_timer,
)

START = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def _started(duration: int = 600) -> SessionTimer:
    return start_timer(
        timer_id="timer-1",
        session_id="session-1",
        step_id="step-1",
        label="incubation",
        duration_seconds=duration,
        at=START,
        correlation_id="correlation-1",
    )


def test_a_started_timer_stores_an_absolute_deadline() -> None:
    timer = _started()

    assert timer.state is TimerState.RUNNING
    assert timer.deadline_at == START + timedelta(seconds=600)
    assert remaining_seconds(timer, now=START) == 600


def test_remaining_time_is_recomputed_from_the_clock_not_a_counter() -> None:
    timer = _started()

    # Equivalent to the backend restarting 90 seconds later and rehydrating.
    assert remaining_seconds(timer, now=START + timedelta(seconds=90)) == 510


def test_remaining_time_never_goes_negative() -> None:
    timer = _started(duration=30)

    assert remaining_seconds(timer, now=START + timedelta(hours=5)) == 0


def test_a_partial_second_is_rounded_up_so_the_operator_is_never_told_zero_early() -> None:
    timer = _started(duration=10)

    assert remaining_seconds(timer, now=START + timedelta(seconds=9.2)) == 1


def test_pausing_freezes_the_remaining_time() -> None:
    timer = _started()

    paused = pause_timer(timer, at=START + timedelta(seconds=100))

    assert paused.state is TimerState.PAUSED
    assert paused.deadline_at is None
    assert paused.remaining_seconds_at_pause == 500
    # Time passing while paused must not consume the timer.
    assert remaining_seconds(paused, now=START + timedelta(hours=2)) == 500


def test_resuming_rebases_the_deadline_on_the_resume_moment() -> None:
    paused = pause_timer(_started(), at=START + timedelta(seconds=100))

    resumed = resume_timer(paused, at=START + timedelta(seconds=1000))

    assert resumed.state is TimerState.RUNNING
    assert resumed.deadline_at == START + timedelta(seconds=1500)
    assert remaining_seconds(resumed, now=START + timedelta(seconds=1000)) == 500


def test_a_timer_that_reached_its_deadline_settles_as_elapsed() -> None:
    timer = _started(duration=60)

    settled = settle_timer(timer, at=START + timedelta(seconds=61))

    assert settled is not None
    assert settled.state is TimerState.ELAPSED
    assert settled.resolved_at == START + timedelta(seconds=61)


def test_settling_a_timer_that_is_still_running_changes_nothing() -> None:
    timer = _started(duration=60)

    assert settle_timer(timer, at=START + timedelta(seconds=59)) is None


def test_settling_a_paused_timer_changes_nothing() -> None:
    paused = pause_timer(_started(duration=60), at=START + timedelta(seconds=10))

    assert settle_timer(paused, at=START + timedelta(days=1)) is None


def test_cancelling_records_the_moment_and_stops_the_countdown() -> None:
    cancelled = cancel_timer(_started(), at=START + timedelta(seconds=5))

    assert cancelled.state is TimerState.CANCELLED
    assert cancelled.resolved_at == START + timedelta(seconds=5)
    assert remaining_seconds(cancelled, now=START + timedelta(seconds=600)) == 0


@pytest.mark.parametrize("duration", [0, -1, 604801])
def test_a_timer_duration_must_be_within_the_supported_range(duration: int) -> None:
    with pytest.raises(DomainError):
        start_timer(
            timer_id="timer-1",
            session_id="session-1",
            step_id=None,
            label="incubation",
            duration_seconds=duration,
            at=START,
            correlation_id="correlation-1",
        )


def test_a_terminal_timer_cannot_be_paused_resumed_or_cancelled_again() -> None:
    cancelled = cancel_timer(_started(), at=START)

    for transition in (pause_timer, resume_timer, cancel_timer):
        with pytest.raises(DomainError):
            transition(cancelled, at=START)


def test_a_running_timer_cannot_be_resumed_and_a_paused_timer_cannot_be_paused() -> None:
    running = _started()
    with pytest.raises(DomainError):
        resume_timer(running, at=START)

    paused = pause_timer(running, at=START)
    with pytest.raises(DomainError):
        pause_timer(paused, at=START)


def test_every_transition_advances_the_optimistic_version_exactly_once() -> None:
    running = _started()
    assert running.version == 0

    paused = pause_timer(running, at=START)
    assert paused.version == 1
    assert resume_timer(paused, at=START).version == 2
    assert cancel_timer(paused, at=START).version == 2


def test_a_naive_timestamp_is_rejected_so_deadlines_stay_comparable() -> None:
    with pytest.raises(DomainError):
        start_timer(
            timer_id="timer-1",
            session_id="session-1",
            step_id=None,
            label="incubation",
            duration_seconds=60,
            at=datetime(2026, 3, 1, 12, 0, 0),
            correlation_id="correlation-1",
        )
