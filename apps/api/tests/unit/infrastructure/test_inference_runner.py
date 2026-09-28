from __future__ import annotations

from threading import Event, Thread, Timer

import pytest

from labjarvis.infrastructure.inference import BoundedInferenceRunner, DirectInferenceRunner
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelUnavailableError,
)


def test_direct_runner_preserves_fake_mode_determinism() -> None:
    runner = DirectInferenceRunner()

    assert runner.run("fake", lambda: "result") == "result"
    runner.close()


def test_bounded_runner_times_out_and_keeps_capacity_bounded() -> None:
    release = Event()
    runner = BoundedInferenceRunner(max_workers=1, timeout_seconds=0.01)

    with pytest.raises(InferenceTimeoutError, match="configured deadline"):
        runner.run("speech.transcribe", lambda: release.wait(timeout=1))

    with pytest.raises(ModelUnavailableError, match="capacity"):
        runner.run("text.interpret", lambda: "must not run")

    release.set()
    runner.close()


def test_bounded_runner_waits_briefly_for_a_busy_slot_instead_of_refusing() -> None:
    """A short overlap (a scan frame during a transcription) is queued, not a 503."""

    started = Event()
    release = Event()
    runner = BoundedInferenceRunner(max_workers=1, timeout_seconds=5)
    first = Thread(
        target=runner.run,
        args=("speech.transcribe", lambda: (started.set(), release.wait(1))),
    )
    first.start()
    started.wait(1)
    Timer(0.2, release.set).start()

    assert runner.run("vision.recognise", lambda: "read") == "read"

    first.join()
    runner.close()
