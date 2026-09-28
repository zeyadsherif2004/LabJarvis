"""Background warm-up never blocks or crashes the application."""

import threading

from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.infrastructure.warmup import WarmupTracker


class Warm:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def warm_up(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class Cascade:
    def __init__(self, inner: Warm) -> None:
        self.language_model = inner


def test_states_are_reported_per_stage() -> None:
    ok, failing = Warm(), Warm(ModelUnavailableError("no weights"))
    tracker = WarmupTracker(
        {"speech": ok, "text": Cascade(Warm()), "vision": failing, "docs": object()}
    )

    assert tracker.snapshot()["speech"] == "pending"
    assert tracker.snapshot()["docs"] == "not_applicable"
    tracker.run()
    state = tracker.snapshot()

    assert state["speech"].startswith("ready")
    assert state["text"].startswith("ready")
    assert state["vision"] == "failed: no weights"
    assert ok.calls == 1


def test_unexpected_errors_are_contained() -> None:
    tracker = WarmupTracker({"speech": Warm(RuntimeError("boom"))})
    tracker.run()

    assert tracker.snapshot()["speech"] == "failed: RuntimeError"


def test_start_uses_a_daemon_thread() -> None:
    started: list[threading.Thread] = []

    def factory(**kwargs: object) -> threading.Thread:
        thread = threading.Thread(**kwargs)  # type: ignore[arg-type]
        started.append(thread)
        return thread

    WarmupTracker({"speech": Warm()}).start(thread_factory=factory)
    started[0].join(timeout=5)

    assert started[0].daemon is True
