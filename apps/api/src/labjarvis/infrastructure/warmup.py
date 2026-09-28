"""Background warm-up of expensive local models, never blocking startup.

The first utterance of a demonstration used to pay the full model-load time
(about 6-14 s cold on the audited laptop). When enabled, each stage that can
warm up is loaded once in a daemon thread after startup. Readiness reports the
state of every stage truthfully: ``pending``, ``warming``, ``ready``,
``failed: <reason>`` or ``not_applicable``. A failed warm-up never stops the
application; the stage simply loads (or fails) on first use as before.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from time import perf_counter
from typing import Protocol, runtime_checkable

from labjarvis.infrastructure.model_errors import ModelAdapterError


@runtime_checkable
class SupportsWarmUp(Protocol):
    def warm_up(self) -> None: ...


def warmable(adapter: object) -> SupportsWarmUp | None:
    """The object that can warm up for ``adapter`` (a cascade warms its model)."""

    if isinstance(adapter, SupportsWarmUp):
        return adapter
    inner: object = getattr(adapter, "language_model", None)
    if isinstance(inner, SupportsWarmUp):
        return inner
    return None


class WarmupTracker:
    """Thread-safe per-stage warm-up state."""

    def __init__(self, stages: dict[str, object]) -> None:
        self._lock = threading.Lock()
        self._targets = {name: warmable(adapter) for name, adapter in stages.items()}
        self._state = {
            name: ("pending" if target is not None else "not_applicable")
            for name, target in self._targets.items()
        }
        self._seconds: dict[str, float] = {}

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {
                name: (
                    f"{state} ({self._seconds[name]:.1f}s)" if name in self._seconds else state
                )
                for name, state in self._state.items()
            }

    def _set(self, name: str, state: str, seconds: float | None = None) -> None:
        with self._lock:
            self._state[name] = state
            if seconds is not None:
                self._seconds[name] = seconds

    def run(self) -> None:
        for name, target in self._targets.items():
            if target is None:
                continue
            self._set(name, "warming")
            started = perf_counter()
            try:
                target.warm_up()
            except ModelAdapterError as exc:
                self._set(name, f"failed: {exc}")
                continue
            except Exception as exc:
                self._set(name, f"failed: {type(exc).__name__}")
                continue
            self._set(name, "ready", perf_counter() - started)

    def start(self, *, thread_factory: Callable[..., threading.Thread] = threading.Thread) -> None:
        thread_factory(target=self.run, name="labjarvis-model-warmup", daemon=True).start()
