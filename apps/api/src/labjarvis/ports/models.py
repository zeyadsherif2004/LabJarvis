"""Contracts for speech, language, and visible-label model adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, TypeVar

from labjarvis.domain.models import ParsedCommand


@dataclass(frozen=True, slots=True)
class CommandContext:
    session_id: str
    session_state: str
    active_step_id: str | None
    active_instruction: str | None


@dataclass(frozen=True, slots=True)
class SpeechResult:
    transcript: str
    confidence: float | None
    adapter: str
    model_name: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class LabelResult:
    visible_text: str
    confidence: float | None
    adapter: str
    model_name: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class OcrPage:
    page: int
    text: str
    width: int
    height: int
    adapter: str
    model_name: str


@dataclass(frozen=True, slots=True)
class TextResult:
    """One proposed command plus the provenance of the adapter that produced it.

    Command understanding returns a result envelope rather than a bare
    ``ParsedCommand`` so per-request provider metadata has a safe, immutable home.
    Storing it on the adapter instead would be unsound: inference runs
    concurrently, so a "last request" attribute could be read by the wrong caller.
    """

    command: ParsedCommand
    adapter: str
    model_name: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class AdapterReadiness:
    """Staged readiness for one model adapter.

    A single boolean was misleading: an importable dependency is not a cached
    model, a cached model is not a loaded one, and none of those prove the
    adapter can actually produce a result. Each stage is reported separately so
    the operator sees which one is missing instead of a bare "not ready", and so
    the UI never claims a capability that has not been demonstrated.

    ``ready`` stays the overall verdict for callers that only need one answer.
    Stages that do not apply to a profile are ``None`` rather than ``False`` -
    a local model has no provider to reach, and a hosted one has nothing to cache.
    """

    ready: bool
    adapter: str
    model_name: str
    detail: str
    configured: bool = True
    dependency_installed: bool | None = None
    model_cached: bool | None = None
    loaded: bool | None = None
    provider_reachable: bool | None = None
    inference_tested: bool | None = None

    @property
    def blocking_stage(self) -> str | None:
        """The first unsatisfied stage, for a message the operator can act on."""

        stages = (
            ("configured", self.configured),
            ("dependency_installed", self.dependency_installed),
            ("model_cached", self.model_cached),
            ("loaded", self.loaded),
            ("provider_reachable", self.provider_reachable),
            ("inference_tested", self.inference_tested),
        )
        for name, value in stages:
            if value is False:
                return name
        return None


class SpeechToTextPort(Protocol):
    adapter_name: str
    model_name: str

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        vocabulary: tuple[str, ...] = (),
    ) -> SpeechResult:
        """Transcribe bounded audio.

        ``vocabulary`` is optional protocol-derived context (reagent names,
        aliases, units) that an adapter may use to bias recognition. Adapters
        that cannot use it must ignore it; it never changes validation.
        """
        ...

    def readiness(self) -> AdapterReadiness: ...


class TextUnderstandingPort(Protocol):
    adapter_name: str
    model_name: str

    def interpret(self, transcript: str, context: CommandContext) -> TextResult: ...

    def readiness(self) -> AdapterReadiness: ...


class LabelRecognitionPort(Protocol):
    adapter_name: str
    model_name: str

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult: ...

    def readiness(self) -> AdapterReadiness: ...


class DocumentOcrPort(Protocol):
    adapter_name: str
    model_name: str

    def extract_pages(self, document: bytes) -> tuple[OcrPage, ...]: ...

    def readiness(self) -> AdapterReadiness: ...


T = TypeVar("T")


class InferenceRunnerPort(Protocol):
    def run(self, operation: str, call: Callable[[], T]) -> T: ...

    def close(self) -> None: ...
