"""Framework-free domain types shared by LabJarvis use cases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Self


class DomainError(ValueError):
    """A requested state change violates a domain invariant."""


class ProvenanceError(DomainError):
    """An append-only record is missing required provenance."""


class SessionState(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class StepState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"


class VerificationOutcome(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNCERTAIN = "UNCERTAIN"


class VerificationDecision(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CORRECTED = "CORRECTED"
    STALE = "STALE"


class EntrySource(StrEnum):
    USER_CONFIRMED_SPEECH = "user_confirmed_speech"
    TYPED_INPUT = "typed_input"
    IMPORTED_PROTOCOL = "imported_protocol"
    MODEL_PROPOSAL = "model_proposal"
    SYSTEM_EVENT = "system_event"


class ProtocolVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class TimerState(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ELAPSED = "ELAPSED"
    CANCELLED = "CANCELLED"


class PendingActionStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class SourceReference:
    page: int
    section: str | None
    excerpt: str
    ocr_derived: bool = False


@dataclass(frozen=True, slots=True)
class ProtocolRequirement:
    """One reagent a step expects.

    ``requirement_id`` is stable for the lifetime of the step so verification,
    audit records, and the UI can address a specific reagent rather than relying
    on list position, which changes whenever a reviewer reorders a draft.
    """

    requirement_id: str
    name: str
    quantity: Decimal | None = None
    unit: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.requirement_id.strip():
            raise DomainError("a protocol requirement needs a stable identifier")


@dataclass(frozen=True, slots=True)
class ExtractionFlag:
    """Something the importer could not settle from the source text alone.

    Import never resolves an ambiguity by picking a value - a range stays a
    range and an unclear timing gets no timer. It records the ambiguity here
    instead, and approval is refused until a reviewer has acknowledged it.
    """

    code: str
    message: str
    excerpt: str | None = None
    acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class UnassignedSourceText:
    """Source text that import did not turn into, or attach to, any step.

    It is kept and shown during review so nothing the document says can
    disappear between upload and approval without the reviewer seeing it.
    """

    page: int
    section: str | None
    text: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProtocolStepDraft:
    id: str
    number: int
    title: str
    instruction: str
    requirements: tuple[ProtocolRequirement, ...]
    duration_seconds: int | None
    safety_notes: tuple[str, ...]
    source: SourceReference
    parallel_allowed: bool = False
    flags: tuple[ExtractionFlag, ...] = ()


@dataclass(frozen=True, slots=True)
class Protocol:
    id: str
    title: str
    created_at: datetime
    archived_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProtocolVersion:
    id: str
    protocol_id: str
    version_number: int
    revision: int
    status: ProtocolVersionStatus
    steps: tuple[ProtocolStepDraft, ...]
    source_sha256: str
    created_at: datetime
    source_text: str = ""
    approved_at: datetime | None = None
    unassigned_text: tuple[UnassignedSourceText, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    intent: str
    parameters: Mapping[str, str]
    confidence: float
    ambiguities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class PendingAction:
    id: str
    session_id: str
    intent: str
    payload: Mapping[str, str]
    source: EntrySource
    status: PendingActionStatus
    expected_session_version: int
    created_at: datetime
    correlation_id: str
    model_inference_id: str
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class ModelInference:
    id: str
    session_id: str | None
    modality: str
    adapter: str
    model_name: str
    status: str
    confidence: float | None
    latency_ms: int
    input_sha256: str
    output: Mapping[str, str]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", MappingProxyType(dict(self.output)))


@dataclass(frozen=True, slots=True)
class ReagentVerification:
    id: str
    session_id: str
    step_id: str
    #: Which reagent of the step this comparison was about. Empty only for rows
    #: written before steps could carry more than one requirement.
    requirement_id: str
    expected_label: str
    observed_label: str
    outcome: VerificationOutcome
    score: float
    expected_normalised: str
    observed_normalised: str
    match_threshold: float
    uncertain_threshold: float
    model_confidence: float | None
    decision: VerificationDecision
    model_inference_id: str
    created_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    session_id: str | None
    event_type: str
    actor: str
    correlation_id: str
    details: Mapping[str, str]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    scope: str
    key: str
    request_sha256: str
    response: Mapping[str, str]
    created_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "response", MappingProxyType(dict(self.response)))


@dataclass(frozen=True, slots=True)
class SessionStep:
    step_id: str
    state: StepState
    started_at: datetime | None = None
    completed_at: datetime | None = None
    skipped_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LabSession:
    id: str
    protocol_version_id: str
    protocol_approved: bool
    state: SessionState
    steps: tuple[SessionStep, ...]
    active_step_id: str | None
    version: int
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class SessionTimer:
    """A durable laboratory timer.

    ``deadline_at`` is authoritative while running and ``remaining_seconds_at_pause``
    while paused; exactly one of the two is set. Remaining time is always derived
    (see :mod:`labjarvis.domain.timers`) so a reload or restart cannot drift it.
    """

    id: str
    session_id: str
    step_id: str | None
    label: str
    duration_seconds: int
    state: TimerState
    created_at: datetime
    started_at: datetime
    deadline_at: datetime | None
    remaining_seconds_at_pause: int | None
    paused_at: datetime | None
    resolved_at: datetime | None
    correlation_id: str
    version: int


@dataclass(frozen=True, slots=True)
class ProtocolSummary:
    """A protocol as it appears on the dashboard, without loading its steps."""

    protocol_id: str
    title: str
    created_at: datetime
    latest_version_id: str
    latest_version_status: ProtocolVersionStatus
    latest_revision: int
    step_count: int
    session_count: int
    archived_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """A resumable session as it appears on the dashboard."""

    session_id: str
    protocol_id: str
    protocol_title: str
    protocol_version_id: str
    state: SessionState
    version: int
    step_count: int
    completed_step_count: int
    active_step_number: int | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    label: str | None = None

    @property
    def resumable(self) -> bool:
        """Whether opening this session can continue work rather than only review it."""

        return self.state in {SessionState.READY, SessionState.ACTIVE, SessionState.PAUSED}


@dataclass(frozen=True, slots=True)
class Utterance:
    """One spoken input, and every reading of it that was taken.

    An utterance exists so a recorded measurement can always be traced back to
    what was actually heard, by which model, and whether a second model agreed.
    ``canonical_transcript`` is the wording the system acted on; it is empty
    whenever the readings conflicted, because in that case nothing was acted on
    until the operator resolved it.
    """

    id: str
    session_id: str
    realtime_transcript: str
    verification_transcript: str
    canonical_transcript: str
    agreement: str
    reasons: tuple[str, ...]
    primary_model: str
    verification_model: str
    primary_inference_id: str
    verification_inference_id: str
    confirmed_transcript: str
    created_at: datetime
    correlation_id: str
    #: "command" when the utterance was interpreted as an instruction,
    #: "confirmation" when it answered a specific proposal.
    purpose: str = "command"
    #: The proposal this utterance answered. Empty for command utterances.
    pending_action_id: str = ""
    #: Hash of the audio. Used to refuse a confirmation that replays the same
    #: recording that produced the proposal.
    audio_sha256: str = ""
    #: The deterministic verdict: confirm, reject, or ambiguous.
    decision: str = ""


@dataclass(frozen=True, slots=True)
class ReagentComparison:
    outcome: VerificationOutcome
    score: float
    expected_normalised: str
    observed_normalised: str
    matched_alias: str | None = None


@dataclass(frozen=True, slots=True)
class NotebookEntry:
    id: str
    session_id: str
    entry_type: str
    source: EntrySource
    payload: Mapping[str, str]
    created_at: datetime
    correlation_id: str
    supersedes_entry_id: str | None = None
    model_inference_id: str | None = None

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ProvenanceError("notebook entry requires a correlation identifier")
        if self.created_at.tzinfo is None:
            raise ProvenanceError("notebook entry timestamp must be timezone-aware")
        copied_payload = dict(self.payload)
        object.__setattr__(self, "payload", MappingProxyType(copied_payload))

    def corrected(
        self,
        *,
        entry_id: str,
        payload: Mapping[str, str],
        source: EntrySource,
        correlation_id: str,
        at: datetime,
    ) -> Self:
        return type(self)(
            id=entry_id,
            session_id=self.session_id,
            entry_type="correction",
            source=source,
            payload=payload,
            created_at=at,
            correlation_id=correlation_id,
            supersedes_entry_id=self.id,
        )
