"""Durable, content-free provenance for failed model calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from labjarvis.domain.models import ModelInference
from labjarvis.ports.repositories import UnitOfWorkPort


@dataclass(frozen=True, slots=True)
class FailedInferenceContext:
    session_id: str | None
    modality: str
    operation: str
    adapter: str
    model_name: str
    latency_ms: int
    input_sha256: str
    correlation_id: str
    error: Exception


class InferenceProvenanceService:
    """Write one bounded failure record in its own transaction."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWorkPort],
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._new_id = new_id

    def record_failure(self, context: FailedInferenceContext) -> ModelInference:
        failure_code = getattr(context.error, "code", "MODEL_INFERENCE_FAILED")
        if not isinstance(failure_code, str):
            failure_code = "MODEL_INFERENCE_FAILED"
        inference = ModelInference(
            id=self._new_id(),
            session_id=context.session_id,
            modality=context.modality,
            adapter=context.adapter[:120] or "unknown-adapter",
            model_name=context.model_name[:160] or "unknown-model",
            status="failed",
            confidence=None,
            latency_ms=max(0, context.latency_ms),
            input_sha256=context.input_sha256,
            output={
                "failure_code": failure_code,
                "operation": context.operation[:80],
                "exception_type": type(context.error).__name__[:80],
                "correlation_id": context.correlation_id[:80],
            },
            created_at=self._clock(),
        )
        with self._uow_factory() as uow:
            uow.inferences.append(inference)
        return inference
