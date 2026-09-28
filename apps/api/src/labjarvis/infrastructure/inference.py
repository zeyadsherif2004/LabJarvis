"""Bound synchronous local model work without leaking vendor concerns upstream."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
from typing import TypeVar

from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelUnavailableError,
)

T = TypeVar("T")


class DirectInferenceRunner:
    """Run deterministic fake adapters inline for fast, stable tests."""

    def run(self, operation: str, call: Callable[[], T]) -> T:
        del operation
        return call()

    def close(self) -> None:
        return None


class BoundedInferenceRunner:
    """Apply a hard caller deadline and a fixed in-process concurrency ceiling."""

    #: How long a call may wait for a free slot before it is refused.
    MAX_QUEUE_SECONDS = 2.0

    def __init__(self, *, max_workers: int, timeout_seconds: float) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="labjarvis-model",
        )
        self._capacity = BoundedSemaphore(max_workers)
        self._timeout_seconds = timeout_seconds
        # Refusing the instant the slot was taken made ordinary overlap fail:
        # a 150 ms label-scan frame arriving while a spoken command was being
        # transcribed got HTTP 503, which broke the scan's run of readings. A
        # short wait absorbs that overlap; it stays bounded (never longer than
        # the inference deadline) so a hung model still cannot build a queue.
        self._queue_seconds = min(self.MAX_QUEUE_SECONDS, timeout_seconds)

    def run(self, operation: str, call: Callable[[], T]) -> T:
        if not self._capacity.acquire(timeout=self._queue_seconds):
            raise ModelUnavailableError("local model inference capacity is currently exhausted")
        try:
            future = self._executor.submit(call)
        except RuntimeError:
            self._capacity.release()
            raise
        future.add_done_callback(lambda completed: self._capacity.release())
        try:
            return future.result(timeout=self._timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            raise InferenceTimeoutError(
                f"{operation} exceeded the configured deadline"
            ) from exc

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
