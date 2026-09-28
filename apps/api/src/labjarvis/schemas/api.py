"""Strict request and response models for the versioned HTTP API."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from labjarvis.application.backup import BackupManifest, BackupPackage, RestoreValidation
from labjarvis.application.confirmation import ConfirmationResult
from labjarvis.application.data_management import DeletionReceipt
from labjarvis.application.reagent_verification import LabelProbe
from labjarvis.application.service import (
    CommandProposal,
    ProtocolSource,
    SessionReview,
    SpokenConfirmationResult,
    SpokenReagentDecisionResult,
    ToolInvocationResult,
)
from labjarvis.application.session_orchestration import (
    RequirementProgress,
    SessionSnapshot,
    TimerView,
)
from labjarvis.application.tools import TOOL_CONTRACT_VERSION, TOOLS, ToolSpec
from labjarvis.domain.models import (
    AuditEvent,
    ExtractionFlag,
    LabSession,
    ModelInference,
    NotebookEntry,
    PendingAction,
    Protocol,
    ProtocolRequirement,
    ProtocolStepDraft,
    ProtocolSummary,
    ProtocolVersion,
    ReagentVerification,
    SessionSummary,
    SessionTimer,
    SourceReference,
    UnassignedSourceText,
    Utterance,
)
from labjarvis.ports.realtime import RealtimeGrant


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemDetail(StrictModel):
    type: str
    title: str
    status: int
    detail: str
    code: str
    correlation_id: str


class ReadinessStageResponse(StrictModel):
    status: Literal["ready", "blocked", "not_applicable"]
    detail: str


class AdapterReadinessResponse(StrictModel):
    ready: bool
    adapter: str
    model_name: str
    detail: str
    configured: ReadinessStageResponse
    installed: ReadinessStageResponse
    cached: ReadinessStageResponse
    loaded: ReadinessStageResponse
    reachable: ReadinessStageResponse
    inference_verified: ReadinessStageResponse
    first_blocking_stage: str | None


class LatencyAggregateResponse(StrictModel):
    sample_count: int
    failed_count: int
    average_ms: float
    p50_ms: int
    p95_ms: int


class LatencyWindowResponse(StrictModel):
    max_samples: int
    sample_count: int
    modalities: dict[str, LatencyAggregateResponse]


def _study_disabled() -> dict[str, str | bool]:
    return {"enabled": False}


class StageProvenanceResponse(StrictModel):
    """Who runs one model stage, stated truthfully for the operator and the demo."""

    provider: str
    provider_label: str
    locality: Literal["fake", "local", "hosted"]
    model: str
    detail: str
    warmup: str


class ReadinessResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    mode: Literal["fake", "local", "hosted"]
    tool_contract_version: str
    interaction: dict[str, str | int | bool]
    privacy: dict[str, bool]
    adapters: dict[str, AdapterReadinessResponse]
    database: dict[str, bool]
    latency_window: LatencyWindowResponse
    hosted_provider: str | None = None
    #: ``fake``, ``local``, ``hosted`` or ``hybrid`` across the three stages.
    profile: str = "fake"
    stages: dict[str, StageProvenanceResponse] = Field(default_factory=dict)
    #: One "Hands-free: ready" line, or exactly what is missing (ADR 0018).
    hands_free: dict[str, bool | str | list[str]] = Field(default_factory=dict)
    study: dict[str, str | bool] = Field(default_factory=_study_disabled)
    #: Who speaks responses (Kokoro or the browser) and whether it is usable.
    speech_output: dict[str, str | bool] = Field(default_factory=dict)


class DeleteConfirmationRequest(StrictModel):
    confirmation: str = Field(min_length=1, max_length=200)


class DeletionReceiptResponse(StrictModel):
    deletion_id: str
    deleted_at: str
    object_type: str
    object_id_hash: str
    cleanup_state: str

    @classmethod
    def from_domain(cls, receipt: DeletionReceipt) -> DeletionReceiptResponse:
        return cls(
            deletion_id=receipt.deletion_id,
            deleted_at=receipt.deleted_at.isoformat(),
            object_type=receipt.object_type,
            object_id_hash=receipt.object_id_hash,
            cleanup_state=receipt.cleanup_state,
        )


class BackupFileResponse(StrictModel):
    path: str
    size: int
    sha256: str


class BackupManifestResponse(StrictModel):
    format_version: str
    created_at: str
    schema_revision: str
    application_commit: str | None
    profile: str
    files: tuple[BackupFileResponse, ...]

    @classmethod
    def from_domain(cls, manifest: BackupManifest) -> BackupManifestResponse:
        return cls(
            format_version=manifest.format_version,
            created_at=manifest.created_at.isoformat(),
            schema_revision=manifest.schema_revision,
            application_commit=manifest.application_commit,
            profile=manifest.profile,
            files=tuple(
                BackupFileResponse(path=item.path, size=item.size, sha256=item.sha256)
                for item in manifest.files
            ),
        )


class BackupPackageResponse(StrictModel):
    backup_name: str
    manifest: BackupManifestResponse

    @classmethod
    def from_domain(cls, package: BackupPackage) -> BackupPackageResponse:
        return cls(
            backup_name=package.backup_name,
            manifest=BackupManifestResponse.from_domain(package.manifest),
        )


class RestoreValidationResponse(StrictModel):
    backup_name: str
    staging_name: str
    manifest: BackupManifestResponse
    activation_required: bool

    @classmethod
    def from_domain(cls, result: RestoreValidation) -> RestoreValidationResponse:
        return cls(
            backup_name=result.backup_name,
            staging_name=result.staging_name,
            manifest=BackupManifestResponse.from_domain(result.manifest),
            activation_required=result.activation_required,
        )


class SourceReferenceModel(StrictModel):
    page: int = Field(ge=1)
    section: str | None = None
    excerpt: str = Field(min_length=1, max_length=1000)
    ocr_derived: bool = False

    @classmethod
    def from_domain(cls, source: SourceReference) -> SourceReferenceModel:
        return cls(
            page=source.page,
            section=source.section,
            excerpt=source.excerpt,
            ocr_derived=source.ocr_derived,
        )

    def to_domain(self) -> SourceReference:
        return SourceReference(
            page=self.page,
            section=self.section,
            excerpt=self.excerpt,
            ocr_derived=self.ocr_derived,
        )


class ExtractionFlagModel(StrictModel):
    """Something import could not settle; approval waits until it is acknowledged."""

    code: str = Field(min_length=1, max_length=60)
    message: str = Field(min_length=1, max_length=500)
    excerpt: str | None = Field(default=None, max_length=1000)
    acknowledged: bool = False

    @classmethod
    def from_domain(cls, flag: ExtractionFlag) -> ExtractionFlagModel:
        return cls(
            code=flag.code,
            message=flag.message,
            excerpt=flag.excerpt,
            acknowledged=flag.acknowledged,
        )

    def to_domain(self) -> ExtractionFlag:
        return ExtractionFlag(
            code=self.code,
            message=self.message,
            excerpt=self.excerpt,
            acknowledged=self.acknowledged,
        )


class UnassignedSourceTextModel(StrictModel):
    page: int
    section: str | None
    text: str
    reason: str

    @classmethod
    def from_domain(cls, item: UnassignedSourceText) -> UnassignedSourceTextModel:
        return cls(page=item.page, section=item.section, text=item.text, reason=item.reason)


class ProtocolRequirementModel(StrictModel):
    """A reagent requirement addressed by a stable identifier.

    ``requirement_id`` is always present on responses. It stays optional on
    review requests so an operator client may submit a newly added requirement
    without inventing an identifier; the enclosing step assigns one by position.
    """

    requirement_id: str | None = Field(default=None, min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=250)
    quantity: Decimal | None = None
    unit: str | None = Field(default=None, max_length=30)
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_domain(cls, requirement: ProtocolRequirement) -> ProtocolRequirementModel:
        return cls(
            requirement_id=requirement.requirement_id,
            name=requirement.name,
            quantity=requirement.quantity,
            unit=requirement.unit,
            aliases=requirement.aliases,
        )

    def to_domain(self, *, step_id: str, position: int) -> ProtocolRequirement:
        return ProtocolRequirement(
            requirement_id=self.requirement_id or f"{step_id}-req-{position}",
            name=self.name,
            quantity=self.quantity,
            unit=self.unit,
            aliases=self.aliases,
        )


class ProtocolStepModel(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=250)
    instruction: str = Field(min_length=1, max_length=5000)
    requirements: tuple[ProtocolRequirementModel, ...] = ()
    duration_seconds: int | None = Field(default=None, ge=1, le=604800)
    safety_notes: tuple[str, ...] = ()
    source: SourceReferenceModel
    parallel_allowed: bool = False
    flags: tuple[ExtractionFlagModel, ...] = Field(default=(), max_length=50)

    @classmethod
    def from_domain(cls, step: ProtocolStepDraft) -> ProtocolStepModel:
        return cls(
            id=step.id,
            number=step.number,
            title=step.title,
            instruction=step.instruction,
            requirements=tuple(
                ProtocolRequirementModel.from_domain(requirement)
                for requirement in step.requirements
            ),
            duration_seconds=step.duration_seconds,
            safety_notes=step.safety_notes,
            source=SourceReferenceModel.from_domain(step.source),
            parallel_allowed=step.parallel_allowed,
            flags=tuple(ExtractionFlagModel.from_domain(flag) for flag in step.flags),
        )

    def to_domain(self) -> ProtocolStepDraft:
        return ProtocolStepDraft(
            id=self.id,
            number=self.number,
            title=self.title,
            instruction=self.instruction,
            requirements=tuple(
                requirement.to_domain(step_id=self.id, position=position)
                for position, requirement in enumerate(self.requirements, start=1)
            ),
            duration_seconds=self.duration_seconds,
            safety_notes=self.safety_notes,
            source=self.source.to_domain(),
            parallel_allowed=self.parallel_allowed,
            flags=tuple(flag.to_domain() for flag in self.flags),
        )


class ProtocolVersionResponse(StrictModel):
    id: str
    protocol_id: str
    version_number: int
    revision: int
    status: str
    steps: tuple[ProtocolStepModel, ...]
    source_sha256: str
    created_at: str
    approved_at: str | None

    @classmethod
    def from_domain(cls, version: ProtocolVersion) -> ProtocolVersionResponse:
        return cls(
            id=version.id,
            protocol_id=version.protocol_id,
            version_number=version.version_number,
            revision=version.revision,
            status=version.status.value,
            steps=tuple(ProtocolStepModel.from_domain(step) for step in version.steps),
            source_sha256=version.source_sha256,
            created_at=version.created_at.isoformat(),
            approved_at=version.approved_at.isoformat() if version.approved_at else None,
        )


class ProtocolSourceResponse(StrictModel):
    """The complete extracted text, for verifying the steps against the document."""

    version_id: str
    source_sha256: str
    text: str
    character_count: int
    # Source text import did not place in any step, so review can show it.
    unassigned_text: tuple[UnassignedSourceTextModel, ...] = ()

    @classmethod
    def from_domain(cls, source: ProtocolSource) -> ProtocolSourceResponse:
        return cls(
            version_id=source.version_id,
            source_sha256=source.source_sha256,
            text=source.text,
            character_count=source.character_count,
            unassigned_text=tuple(
                UnassignedSourceTextModel.from_domain(item) for item in source.unassigned_text
            ),
        )


class ProtocolImportResponse(StrictModel):
    protocol_id: str
    title: str
    version: ProtocolVersionResponse


class ProtocolResponse(StrictModel):
    id: str
    title: str
    created_at: str
    archived_at: str | None

    @classmethod
    def from_domain(cls, protocol: Protocol) -> ProtocolResponse:
        return cls(
            id=protocol.id,
            title=protocol.title,
            created_at=protocol.created_at.isoformat(),
            archived_at=protocol.archived_at.isoformat() if protocol.archived_at else None,
        )


class ReviewProtocolRequest(StrictModel):
    steps: tuple[ProtocolStepModel, ...] = Field(min_length=1, max_length=500)
    # The application layer owns the 120-character business limit so callers
    # receive the stable SESSION_LABEL_TOO_LONG code instead of a generic 422.
    label: str | None = Field(default=None, max_length=500)


class CreateSessionRequest(StrictModel):
    protocol_version_id: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=500)


class SessionStepResponse(StrictModel):
    step_id: str
    state: str
    started_at: str | None
    completed_at: str | None
    skipped_reason: str | None


class SessionResponse(StrictModel):
    id: str
    protocol_version_id: str
    state: str
    steps: tuple[SessionStepResponse, ...]
    active_step_id: str | None
    version: int
    created_at: str
    started_at: str | None
    ended_at: str | None
    label: str | None

    @classmethod
    def from_domain(cls, session: LabSession) -> SessionResponse:
        return cls(
            id=session.id,
            protocol_version_id=session.protocol_version_id,
            state=session.state.value,
            steps=tuple(
                SessionStepResponse(
                    step_id=step.step_id,
                    state=step.state.value,
                    started_at=step.started_at.isoformat() if step.started_at else None,
                    completed_at=step.completed_at.isoformat() if step.completed_at else None,
                    skipped_reason=step.skipped_reason,
                )
                for step in session.steps
            ),
            active_step_id=session.active_step_id,
            version=session.version,
            created_at=session.created_at.isoformat(),
            started_at=session.started_at.isoformat() if session.started_at else None,
            ended_at=session.ended_at.isoformat() if session.ended_at else None,
            label=session.label,
        )


class TextCommandRequest(StrictModel):
    transcript: str = Field(min_length=1, max_length=5000)


class PendingActionResponse(StrictModel):
    id: str
    session_id: str
    intent: str
    payload: dict[str, str]
    source: str
    status: str
    expected_session_version: int
    created_at: str
    correlation_id: str

    @classmethod
    def from_domain(cls, action: PendingAction) -> PendingActionResponse:
        return cls(
            id=action.id,
            session_id=action.session_id,
            intent=action.intent,
            payload=dict(action.payload),
            source=action.source.value,
            status=action.status.value,
            expected_session_version=action.expected_session_version,
            created_at=action.created_at.isoformat(),
            correlation_id=action.correlation_id,
        )


class CommandResponse(StrictModel):
    transcript: str
    intent: str
    confidence: float
    ambiguities: tuple[str, ...]
    pending_action: PendingActionResponse | None
    response_text: str
    replayed: bool
    #: Resolved references the client acts on (for example which reagent
    #: requirement a spoken "check the ethanol" selected). Never a new value.
    parameters: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, proposal: CommandProposal) -> CommandResponse:
        return cls(
            parameters={
                key: value
                for key, value in proposal.parsed_command.parameters.items()
                if key in {"requirement_id", "reagent_name"}
            },
            transcript=proposal.transcript,
            intent=proposal.parsed_command.intent,
            confidence=proposal.parsed_command.confidence,
            ambiguities=proposal.parsed_command.ambiguities,
            pending_action=(
                PendingActionResponse.from_domain(proposal.pending_action)
                if proposal.pending_action
                else None
            ),
            response_text=proposal.response_text,
            replayed=proposal.replayed,
        )


class ConfirmPendingActionRequest(StrictModel):
    accepted: bool


class NotebookEntryResponse(StrictModel):
    id: str
    session_id: str
    entry_type: str
    source: str
    payload: dict[str, str]
    created_at: str
    correlation_id: str
    supersedes_entry_id: str | None
    model_inference_id: str | None

    @classmethod
    def from_domain(cls, entry: NotebookEntry) -> NotebookEntryResponse:
        return cls(
            id=entry.id,
            session_id=entry.session_id,
            entry_type=entry.entry_type,
            source=entry.source.value,
            payload=dict(entry.payload),
            created_at=entry.created_at.isoformat(),
            correlation_id=entry.correlation_id,
            supersedes_entry_id=entry.supersedes_entry_id,
            model_inference_id=entry.model_inference_id,
        )


class ConfirmationResponse(StrictModel):
    session: SessionResponse
    pending_action: PendingActionResponse
    entry: NotebookEntryResponse | None

    @classmethod
    def from_domain(cls, result: ConfirmationResult) -> ConfirmationResponse:
        return cls(
            session=SessionResponse.from_domain(result.session),
            pending_action=PendingActionResponse.from_domain(result.pending_action),
            entry=(NotebookEntryResponse.from_domain(result.entry) if result.entry else None),
        )


class ReagentVerificationResponse(StrictModel):
    id: str
    session_id: str
    step_id: str
    requirement_id: str
    expected_label: str
    observed_label: str
    outcome: str
    score: float
    expected_normalised: str
    observed_normalised: str
    match_threshold: float
    uncertain_threshold: float
    model_confidence: float | None
    decision: str
    created_at: str
    resolved_at: str | None

    @classmethod
    def from_domain(cls, result: ReagentVerification) -> ReagentVerificationResponse:
        return cls(
            id=result.id,
            session_id=result.session_id,
            step_id=result.step_id,
            requirement_id=result.requirement_id,
            expected_label=result.expected_label,
            observed_label=result.observed_label,
            outcome=result.outcome.value,
            score=result.score,
            expected_normalised=result.expected_normalised,
            observed_normalised=result.observed_normalised,
            match_threshold=result.match_threshold,
            uncertain_threshold=result.uncertain_threshold,
            model_confidence=result.model_confidence,
            decision=result.decision.value,
            created_at=result.created_at.isoformat(),
            resolved_at=result.resolved_at.isoformat() if result.resolved_at else None,
        )


class ProtocolSummaryResponse(StrictModel):
    """One dashboard row. Deliberately excludes steps so listing stays cheap."""

    protocol_id: str
    title: str
    created_at: str
    latest_version_id: str
    latest_version_status: str
    latest_revision: int
    step_count: int
    session_count: int
    archived_at: str | None

    @classmethod
    def from_domain(cls, summary: ProtocolSummary) -> ProtocolSummaryResponse:
        return cls(
            protocol_id=summary.protocol_id,
            title=summary.title,
            created_at=summary.created_at.isoformat(),
            latest_version_id=summary.latest_version_id,
            latest_version_status=summary.latest_version_status.value,
            latest_revision=summary.latest_revision,
            step_count=summary.step_count,
            session_count=summary.session_count,
            archived_at=summary.archived_at.isoformat() if summary.archived_at else None,
        )


class ProtocolListResponse(StrictModel):
    protocols: tuple[ProtocolSummaryResponse, ...]

    @classmethod
    def from_domain(cls, summaries: tuple[ProtocolSummary, ...]) -> ProtocolListResponse:
        return cls(
            protocols=tuple(ProtocolSummaryResponse.from_domain(item) for item in summaries)
        )


class SessionSummaryResponse(StrictModel):
    session_id: str
    protocol_id: str
    protocol_title: str
    protocol_version_id: str
    state: str
    version: int
    step_count: int
    completed_step_count: int
    active_step_number: int | None
    resumable: bool
    created_at: str
    started_at: str | None
    ended_at: str | None
    label: str | None

    @classmethod
    def from_domain(cls, summary: SessionSummary) -> SessionSummaryResponse:
        return cls(
            session_id=summary.session_id,
            protocol_id=summary.protocol_id,
            protocol_title=summary.protocol_title,
            protocol_version_id=summary.protocol_version_id,
            state=summary.state.value,
            version=summary.version,
            step_count=summary.step_count,
            completed_step_count=summary.completed_step_count,
            active_step_number=summary.active_step_number,
            resumable=summary.resumable,
            created_at=summary.created_at.isoformat(),
            started_at=summary.started_at.isoformat() if summary.started_at else None,
            ended_at=summary.ended_at.isoformat() if summary.ended_at else None,
            label=summary.label,
        )


class SessionListResponse(StrictModel):
    sessions: tuple[SessionSummaryResponse, ...]

    @classmethod
    def from_domain(cls, summaries: tuple[SessionSummary, ...]) -> SessionListResponse:
        return cls(
            sessions=tuple(SessionSummaryResponse.from_domain(item) for item in summaries)
        )


class TimerResponse(StrictModel):
    """A timer with the remaining seconds computed server-side.

    The client may count down locally for smoothness, but the server value is
    authoritative: it is what survives a reload, and what a restarted backend
    recomputes from the stored deadline.
    """

    id: str
    session_id: str
    step_id: str | None
    label: str
    duration_seconds: int
    state: str
    remaining_seconds: int
    #: Elapsed and not yet acknowledged; derived from durable state every read.
    alarm_active: bool = False
    started_at: str
    deadline_at: str | None
    paused_at: str | None
    resolved_at: str | None

    @classmethod
    def from_domain(cls, view: TimerView) -> TimerResponse:
        timer: SessionTimer = view.timer
        return cls(
            id=timer.id,
            session_id=timer.session_id,
            step_id=timer.step_id,
            label=timer.label,
            duration_seconds=timer.duration_seconds,
            state=timer.state.value,
            remaining_seconds=view.remaining_seconds,
            alarm_active=view.alarm_active,
            started_at=timer.started_at.isoformat(),
            deadline_at=timer.deadline_at.isoformat() if timer.deadline_at else None,
            paused_at=timer.paused_at.isoformat() if timer.paused_at else None,
            resolved_at=timer.resolved_at.isoformat() if timer.resolved_at else None,
        )


class TimerListResponse(StrictModel):
    timers: tuple[TimerResponse, ...]

    @classmethod
    def from_domain(cls, views: tuple[TimerView, ...]) -> TimerListResponse:
        return cls(timers=tuple(TimerResponse.from_domain(view) for view in views))


class InvokeToolRequest(StrictModel):
    """Arguments for one gateway invocation.

    Values are strings because every tool argument is validated and normalised
    deterministically by the application layer rather than trusted from its wire
    type.
    """

    parameters: dict[str, str] = Field(default_factory=dict)


class ToolInvocationResponse(StrictModel):
    """Whether the action was applied or is waiting for a human.

    ``committed`` and ``pending_action`` are mutually exclusive by construction,
    so no caller - including the realtime layer - can report a proposal as done.
    """

    tool: str
    committed: bool
    replayed: bool
    message: str
    session: SessionResponse
    pending_action: PendingActionResponse | None
    timers: tuple[TimerResponse, ...]
    entry: NotebookEntryResponse | None
    data: dict[str, str]

    @classmethod
    def from_domain(cls, result: ToolInvocationResult) -> ToolInvocationResponse:
        return cls(
            tool=result.tool,
            committed=result.committed,
            replayed=result.replayed,
            message=result.message,
            session=SessionResponse.from_domain(result.session),
            pending_action=(
                PendingActionResponse.from_domain(result.pending_action)
                if result.pending_action is not None
                else None
            ),
            timers=tuple(TimerResponse.from_domain(view) for view in result.timers),
            entry=(
                NotebookEntryResponse.from_domain(result.entry)
                if result.entry is not None
                else None
            ),
            data=dict(result.data),
        )


class SessionSnapshotResponse(StrictModel):
    """Everything needed to restore a session view from authority alone."""

    session: SessionResponse
    protocol_id: str
    protocol_title: str
    version: ProtocolVersionResponse
    timers: tuple[TimerResponse, ...]
    pending_action: PendingActionResponse | None
    pending_verification: ReagentVerificationResponse | None
    requirement_progress: tuple[RequirementProgressResponse, ...]
    entries: tuple[NotebookEntryResponse, ...]

    @classmethod
    def from_domain(cls, snapshot: SessionSnapshot) -> SessionSnapshotResponse:
        return cls(
            session=SessionResponse.from_domain(snapshot.session),
            protocol_id=snapshot.protocol.id,
            protocol_title=snapshot.protocol.title,
            version=ProtocolVersionResponse.from_domain(snapshot.version),
            timers=tuple(TimerResponse.from_domain(view) for view in snapshot.timers),
            pending_action=(
                PendingActionResponse.from_domain(snapshot.pending_action)
                if snapshot.pending_action is not None
                else None
            ),
            pending_verification=(
                ReagentVerificationResponse.from_domain(snapshot.pending_verification)
                if snapshot.pending_verification is not None
                else None
            ),
            requirement_progress=tuple(
                RequirementProgressResponse.from_domain(item)
                for item in snapshot.requirement_progress
            ),
            entries=tuple(
                NotebookEntryResponse.from_domain(entry) for entry in snapshot.entries
            ),
        )


class RequirementProgressResponse(StrictModel):
    requirement_id: str
    name: str
    status: str
    verification_id: str | None

    @classmethod
    def from_domain(cls, progress: RequirementProgress) -> RequirementProgressResponse:
        return cls(
            requirement_id=progress.requirement_id,
            name=progress.name,
            status=progress.status,
            verification_id=progress.verification_id,
        )


class ApproveAndStartResponse(StrictModel):
    """The result of the single transactional approve-then-open-session step."""

    version: ProtocolVersionResponse
    session: SessionResponse


class ToolDescriptor(StrictModel):
    """One entry of the shared allowlist, published so the UI can stay in step."""

    name: str
    version: str
    kind: str
    confirmation: str
    summary: str
    legal_session_states: tuple[str, ...]
    required_parameters: tuple[str, ...]
    realtime_exposed: bool

    @classmethod
    def from_domain(cls, spec: ToolSpec) -> ToolDescriptor:
        return cls(
            name=spec.name,
            version=spec.version,
            kind=spec.kind.value,
            confirmation=spec.confirmation.value,
            summary=spec.summary,
            legal_session_states=tuple(
                sorted(state.value for state in spec.legal_session_states)
            ),
            required_parameters=spec.required_parameters,
            realtime_exposed=spec.realtime_exposed,
        )


class ToolRegistryResponse(StrictModel):
    contract_version: str
    tools: tuple[ToolDescriptor, ...]

    @classmethod
    def current(cls) -> ToolRegistryResponse:
        return cls(
            contract_version=TOOL_CONTRACT_VERSION,
            tools=tuple(ToolDescriptor.from_domain(spec) for spec in TOOLS.values()),
        )


class RealtimeSessionRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=36)


class RealtimeSessionResponse(StrictModel):
    """A short-lived grant plus the authoritative state to start from.

    ``client_secret`` is deliberately short-lived and scoped. It is the only
    provider credential that ever reaches a browser; the account key stays on
    the server by construction.
    """

    client_secret: str
    expires_at: str
    model_name: str
    voice: str
    transport: str
    provider: str
    provider_session_id: str
    tool_names: tuple[str, ...]
    max_utterance_seconds: int
    idle_timeout_seconds: int
    snapshot: SessionSnapshotResponse

    @classmethod
    def from_domain(
        cls,
        grant: RealtimeGrant,
        snapshot: SessionSnapshot,
        *,
        max_utterance_seconds: int = 30,
        idle_timeout_seconds: int = 300,
    ) -> RealtimeSessionResponse:
        return cls(
            client_secret=grant.client_secret,
            expires_at=grant.expires_at.isoformat(),
            model_name=grant.model_name,
            voice=grant.voice,
            transport=grant.transport,
            provider=str(grant.metadata.get("provider", "")),
            provider_session_id=grant.provider_session_id,
            tool_names=grant.tool_names,
            max_utterance_seconds=max_utterance_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            snapshot=SessionSnapshotResponse.from_domain(snapshot),
        )


class ConfirmReagentVerificationRequest(StrictModel):
    accepted: bool
    corrected_observed_label: str | None = Field(default=None, min_length=1, max_length=250)


class UtteranceResponse(StrictModel):
    """One spoken input and every reading that was taken of it.

    This is what lets an operator answer "where did that number come from":
    which model produced the first reading, which produced the independent
    second reading, whether they agreed, and what the system therefore acted on.
    Transcript text is present only when transcript retention is enabled.
    """

    id: str
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
    created_at: str
    correlation_id: str
    purpose: str
    pending_action_id: str
    decision: str

    @classmethod
    def from_domain(cls, utterance: Utterance) -> UtteranceResponse:
        return cls(
            id=utterance.id,
            realtime_transcript=utterance.realtime_transcript,
            verification_transcript=utterance.verification_transcript,
            canonical_transcript=utterance.canonical_transcript,
            agreement=utterance.agreement,
            reasons=tuple(utterance.reasons),
            primary_model=utterance.primary_model,
            verification_model=utterance.verification_model,
            primary_inference_id=utterance.primary_inference_id,
            verification_inference_id=utterance.verification_inference_id,
            confirmed_transcript=utterance.confirmed_transcript,
            created_at=utterance.created_at.isoformat(),
            correlation_id=utterance.correlation_id,
            purpose=utterance.purpose,
            pending_action_id=utterance.pending_action_id,
            decision=utterance.decision,
        )


class NotebookResponse(StrictModel):
    protocol_version_id: str
    entries: tuple[NotebookEntryResponse, ...]
    inferences: tuple[ModelInferenceResponse, ...]
    verifications: tuple[ReagentVerificationResponse, ...]
    audit_events: tuple[AuditEventResponse, ...]
    utterances: tuple[UtteranceResponse, ...] = ()

    @classmethod
    def from_domain(cls, review: SessionReview) -> NotebookResponse:
        return cls(
            protocol_version_id=review.session.protocol_version_id,
            entries=tuple(NotebookEntryResponse.from_domain(item) for item in review.entries),
            inferences=tuple(
                ModelInferenceResponse.from_domain(item) for item in review.inferences
            ),
            verifications=tuple(
                ReagentVerificationResponse.from_domain(item) for item in review.verifications
            ),
            audit_events=tuple(
                AuditEventResponse.from_domain(item) for item in review.audit_events
            ),
            utterances=tuple(
                UtteranceResponse.from_domain(item) for item in review.utterances
            ),
        )


class ModelInferenceResponse(StrictModel):
    id: str
    modality: str
    adapter: str
    model_name: str
    status: str
    confidence: float | None
    latency_ms: int
    input_sha256: str
    output: dict[str, str]
    created_at: str

    @classmethod
    def from_domain(cls, inference: ModelInference) -> ModelInferenceResponse:
        return cls(
            id=inference.id,
            modality=inference.modality,
            adapter=inference.adapter,
            model_name=inference.model_name,
            status=inference.status,
            confidence=inference.confidence,
            latency_ms=inference.latency_ms,
            input_sha256=inference.input_sha256,
            output=dict(inference.output),
            created_at=inference.created_at.isoformat(),
        )


class AuditEventResponse(StrictModel):
    id: str
    event_type: str
    actor: str
    correlation_id: str
    details: dict[str, str]
    created_at: str

    @classmethod
    def from_domain(cls, event: AuditEvent) -> AuditEventResponse:
        return cls(
            id=event.id,
            event_type=event.event_type,
            actor=event.actor,
            correlation_id=event.correlation_id,
            details=dict(event.details),
            created_at=event.created_at.isoformat(),
        )


class LabelProbeResponse(StrictModel):
    """One live-scan reading. Not a verification and never persisted."""

    outcome: str
    observed_normalised: str
    visible_text: str
    score: float
    requirement_id: str
    expected_label: str

    @classmethod
    def from_domain(cls, probe: LabelProbe) -> LabelProbeResponse:
        return cls(
            outcome=probe.outcome,
            observed_normalised=probe.observed_normalised,
            visible_text=probe.visible_text,
            score=probe.score,
            requirement_id=probe.requirement_id,
            expected_label=probe.expected_label,
        )


class SpokenReagentDecisionResponse(StrictModel):
    """What a spoken answer to a label reading did; ``committed`` is authoritative."""

    decision: str
    committed: bool
    verification: ReagentVerificationResponse
    response_text: str
    reasons: tuple[str, ...]
    control: str = ""

    @classmethod
    def from_domain(cls, result: SpokenReagentDecisionResult) -> SpokenReagentDecisionResponse:
        return cls(
            control=result.control,
            decision=result.decision,
            committed=result.committed,
            verification=ReagentVerificationResponse.from_domain(result.verification),
            response_text=result.response_text,
            reasons=result.reasons,
        )


class SpokenConfirmationResponse(StrictModel):
    """What a spoken reply to a proposal actually did.

    ``committed`` is authoritative and ``response_text`` is the exact wording the
    voice layer must speak. A realtime model that paraphrases this is describing
    its own belief rather than the database, which is the failure mode the whole
    confirmation design exists to prevent.
    """

    decision: str
    committed: bool
    session: SessionResponse
    pending_action: PendingActionResponse
    entry: NotebookEntryResponse | None
    response_text: str
    utterance_id: str
    heard: str
    reasons: tuple[str, ...]
    replayed: bool
    #: Non-empty when the reply was an interface control; nothing was decided.
    control: str = ""

    @classmethod
    def from_domain(cls, result: SpokenConfirmationResult) -> SpokenConfirmationResponse:
        return cls(
            control=result.control,
            decision=result.decision,
            committed=result.committed,
            session=SessionResponse.from_domain(result.session),
            pending_action=PendingActionResponse.from_domain(result.pending_action),
            entry=(
                None
                if result.entry is None
                else NotebookEntryResponse.from_domain(result.entry)
            ),
            response_text=result.response_text,
            utterance_id=result.utterance_id,
            heard=result.heard,
            reasons=tuple(result.reasons),
            replayed=result.replayed,
        )


class CorrectNotebookEntryRequest(StrictModel):
    payload: dict[str, str] = Field(min_length=1, max_length=100)
