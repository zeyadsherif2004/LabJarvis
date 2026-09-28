"""Safe, typed failures at local model boundaries."""


class ModelAdapterError(RuntimeError):
    """A model operation failed without exposing sensitive input in its message."""

    code = "MODEL_ADAPTER_ERROR"
    labjarvis_operation: str | None = None
    labjarvis_latency_ms: int = 0


class ModelInputError(ModelAdapterError, ValueError):
    """Media supplied to an adapter is malformed or unsupported."""

    code = "MODEL_INPUT_INVALID"


class ModelUnavailableError(ModelAdapterError):
    """An optional dependency, local service, or model weight is unavailable."""

    code = "MODEL_UNAVAILABLE"


class ModelOutputError(ModelAdapterError):
    """A model response did not satisfy the trusted output contract."""

    code = "MODEL_OUTPUT_INVALID"


class InferenceTimeoutError(ModelAdapterError):
    """A bounded model operation exceeded its configured deadline."""

    code = "INFERENCE_TIMEOUT"
