"""Transactional protocol, session, multimodal, and notebook use cases."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any

from labjarvis.application.confirmation import ConfirmationResult, ConfirmationService
from labjarvis.application.errors import ApplicationError
from labjarvis.application.inference_provenance import (
    FailedInferenceContext,
    InferenceProvenanceService,
)
from labjarvis.application.notebook_export import (
    NotebookExport,
    NotebookExportError,
    NotebookExportService,
)
from labjarvis.application.protocol_lifecycle import (
    ProtocolLifecycleError,
    ProtocolLifecycleService,
)
from labjarvis.application.reagent_verification import LabelProbe, ReagentVerificationService
from labjarvis.application.session_orchestration import (
    ALARM_ACKNOWLEDGED_EVENT,
    RequirementProgress,
    SessionOrchestrationService,
    SessionSnapshot,
    TimerView,
    derive_requirement_progress,
)
from labjarvis.application.tools import (
    ToolKind,
    ToolSpec,
    get_tool,
    tool_for_intent,
)
from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.domain.confirmations import (
    READBACK_REQUIRED_INTENTS,
    ConfirmationIntent,
    check_measurement_readback,
    read_confirmation,
    readback_prompt,
)
from labjarvis.domain.grounding import compose_answer, retrieve, spoken_answer
from labjarvis.domain.models import (
    AuditEvent,
    DomainError,
    EntrySource,
    IdempotencyRecord,
    LabSession,
    ModelInference,
    NotebookEntry,
    ParsedCommand,
    PendingAction,
    PendingActionStatus,
    Protocol,
    ProtocolRequirement,
    ProtocolStepDraft,
    ProtocolSummary,
    ProtocolVersion,
    ProtocolVersionStatus,
    ReagentVerification,
    SessionState,
    SessionStep,
    SessionSummary,
    StepState,
    TimerState,
    UnassignedSourceText,
    Utterance,
)
from labjarvis.domain.protocol_validation import (
    ProtocolValidationError,
    validate_protocol_for_approval,
    validate_protocol_steps,
)
from labjarvis.domain.references import match_requirement, match_timers
from labjarvis.domain.speech_vocabulary import command_vocabulary, protocol_vocabulary
from labjarvis.domain.state_machine import (
    complete_active_step,
    skip_active_step,
    transition_session,
)
from labjarvis.domain.timers import cancel_timer, pause_timer, resume_timer, start_timer
from labjarvis.domain.transcripts import (
    compare_transcripts,
    salient_tokens,
)
from labjarvis.domain.voice_confirmation import (
    DUAL_AGREED,
    ExpectedReadback,
    VoiceDecision,
    adjectival_duration,
    expected_readback,
    judge_voice_only_confirmation,
    reagent_readback,
    spoken_duration,
    unit_words,
)
from labjarvis.domain.voice_controls import (
    CONTROL_RESPONSES,
    ListenMode,
    VoiceControl,
    control_allowed,
    voice_control,
)
from labjarvis.domain.wake_phrase import addressed_to_assistant, strip_wake_phrase
from labjarvis.infrastructure.documents import (
    DocumentError,
    DocumentExtractor,
)
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.protocol_import import parse_protocol
from labjarvis.ports.media import RawMediaStorageError, RawMediaStorePort, StoredRawMedia
from labjarvis.ports.models import (
    CommandContext,
    DocumentOcrPort,
    InferenceRunnerPort,
    LabelRecognitionPort,
    SpeechToTextPort,
    TextUnderstandingPort,
)
from labjarvis.ports.repositories import UnitOfWorkPort


@dataclass(frozen=True, slots=True)
class ProtocolImportResult:
    protocol: Protocol
    version: ProtocolVersion


@dataclass(frozen=True, slots=True)
class ProtocolSource:
    """The complete extracted text a version was structured from.

    Review used to show a one-line excerpt per step, which is enough to spot a
    typo and not nearly enough to notice that a whole paragraph of the original
    document was never captured as a step at all. The reviewer is signing off
    that the structured steps *are* the protocol, so they need the document.
    """

    version_id: str
    source_sha256: str
    text: str
    character_count: int
    unassigned_text: tuple[UnassignedSourceText, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandProposal:
    transcript: str
    parsed_command: ParsedCommand
    pending_action: PendingAction | None
    response_text: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SpokenConfirmationResult:
    """The outcome of answering one proposal out loud.

    ``committed`` is the only field that says anything happened. A voice layer
    must read ``response_text`` back verbatim rather than describing the outcome
    in its own words, because the whole arrangement depends on the operator
    hearing what the database did, not what a model believes it did.
    """

    decision: str
    committed: bool
    session: LabSession
    pending_action: PendingAction
    entry: NotebookEntry | None
    response_text: str
    utterance_id: str
    heard: str
    reasons: tuple[str, ...] = ()
    replayed: bool = False
    #: Set when the reply was an interface control (e.g. ``STOP_SPEAKING``);
    #: the proposal is untouched and still waiting.
    control: str = ""


@dataclass(frozen=True, slots=True)
class SpokenReagentDecisionResult:
    """The outcome of answering a label reading out loud (voice-only mode).

    ``committed`` is true only when the reading was confirmed or rejected in the
    record. A MISMATCH or UNCERTAIN reading can be rejected by voice but never
    confirmed: confirming a reagent by voice means "this is the right bottle".
    """

    decision: str
    committed: bool
    verification: ReagentVerification
    response_text: str
    reasons: tuple[str, ...] = ()
    control: str = ""


@dataclass(frozen=True, slots=True)
class ToolInvocationResult:
    """The outcome of one gateway invocation.

    ``committed`` distinguishes a state change that has already been written from
    a proposal awaiting a human decision. The UI reports these differently, and
    the realtime layer must never claim the former when it produced the latter.
    """

    tool: str
    committed: bool
    session: LabSession
    pending_action: PendingAction | None
    timers: tuple[TimerView, ...]
    message: str
    entry: NotebookEntry | None = None
    data: Mapping[str, str] = field(default_factory=dict)
    replayed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class SessionReview:
    session: LabSession
    entries: tuple[NotebookEntry, ...]
    inferences: tuple[ModelInference, ...]
    verifications: tuple[ReagentVerification, ...]
    audit_events: tuple[AuditEvent, ...]
    utterances: tuple[Utterance, ...] = ()


UowFactory = Callable[[], UnitOfWorkPort]


class LabJarvisService:
    """Application facade whose mutations each execute in one unit of work."""

    def __init__(
        self,
        *,
        uow_factory: UowFactory,
        document_extractor: DocumentExtractor,
        speech: SpeechToTextPort,
        text: TextUnderstandingPort,
        verification_speech: SpeechToTextPort | None = None,
        labels: LabelRecognitionPort,
        inference_runner: InferenceRunnerPort,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
        command_confidence_threshold: float,
        reagent_match_threshold: float,
        reagent_uncertain_threshold: float,
        raw_media_store: RawMediaStorePort,
        retain_transcripts: bool,
        wake_phrase: str = "Jarvis",
        application_commit: str | None = None,
        document_ocr: DocumentOcrPort | None = None,
        voice_only_confirmation: bool = False,
        live_label_scan: bool = False,
        voice_observations: bool = False,
    ) -> None:
        self._uow_factory = uow_factory
        # Opt-in and weaker than a value read-back: lets "Jarvis, confirm
        # observation" commit a dictated observation both transcriptions agreed on.
        self._voice_observations = voice_observations
        # Opt-in and local-OCR only (enforced in settings): probes are unpaid,
        # unrecorded frame readings used to find a stable label reading.
        self._live_label_scan = live_label_scan
        # Opt-in: lets the wake-phrase listener answer a waiting proposal with a
        # value-bearing confirmation ("Jarvis, confirm 10 millilitres").
        self._voice_only_confirmation = voice_only_confirmation
        self._documents = document_extractor
        self._document_ocr = document_ocr
        self._speech = speech
        # Used only for utterances whose exact wording could change the
        # experimental record. ``None`` disables cross-checking entirely.
        self._verification_speech = verification_speech
        self._text = text
        self._labels = labels
        self._inference = inference_runner
        self._clock = clock
        self._new_id = new_id
        self._inference_provenance = InferenceProvenanceService(
            uow_factory=uow_factory,
            clock=clock,
            new_id=new_id,
        )
        self._command_threshold = command_confidence_threshold
        self._wake_phrase = wake_phrase
        self._reagent_match_threshold = reagent_match_threshold
        self._reagent_uncertain_threshold = reagent_uncertain_threshold
        self._raw_media = raw_media_store
        self._retain_transcripts = retain_transcripts
        self._notebook_export = NotebookExportService(
            uow_factory=uow_factory,
            clock=clock,
            application_commit=application_commit,
        )
        self._protocol_lifecycle = ProtocolLifecycleService(
            uow_factory=uow_factory,
            clock=clock,
            new_id=new_id,
        )
        self._session_orchestration = SessionOrchestrationService(
            uow_factory=uow_factory,
            clock=clock,
            new_id=new_id,
        )
        self._reagent_verification = ReagentVerificationService(
            uow_factory=uow_factory,
            labels=labels,
            inference_runner=inference_runner,
            inference_provenance=self._inference_provenance,
            clock=clock,
            new_id=new_id,
            match_threshold=reagent_match_threshold,
            uncertain_threshold=reagent_uncertain_threshold,
            raw_media_store=raw_media_store,
        )
        self._confirmation = ConfirmationService(
            uow_factory=uow_factory,
            clock=clock,
            new_id=new_id,
            apply_confirmed_action=self._apply_confirmed_action,
        )

    def import_protocol(
        self,
        filename: str,
        content_type: str,
        data: bytes,
        *,
        allow_ocr: bool = False,
        correlation_id: str | None = None,
    ) -> ProtocolImportResult:
        if allow_ocr:
            started_at = perf_counter()
            try:
                extracted = self._inference.run(
                    "document_ocr",
                    lambda: self._documents.extract(
                        filename,
                        content_type,
                        data,
                        allow_ocr=True,
                    ),
                )
            except (DocumentError, ModelAdapterError) as exc:
                adapter = self._document_ocr
                self._inference_provenance.record_failure(
                    FailedInferenceContext(
                        session_id=None,
                        modality="document_ocr",
                        operation="document_ocr.extract_pages",
                        adapter=str(
                            getattr(adapter, "adapter_name", "unknown-document-ocr")
                        ),
                        model_name=str(getattr(adapter, "model_name", "unknown-model")),
                        latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
                        input_sha256=sha256(data).hexdigest(),
                        correlation_id=correlation_id or self._new_id(),
                        error=exc,
                    )
                )
                raise
        else:
            extracted = self._documents.extract(filename, content_type, data)
        parsed = parse_protocol(extracted)
        # The parser numbers steps sequentially and bounds every field, so this
        # should never fail; checking here means a parser defect is reported at
        # upload instead of surfacing later as a draft that cannot be saved.
        steps = self._validate_reviewed_steps(parsed.steps)
        now = self._clock()
        title = Path(filename).stem.strip()[:250] or "Imported protocol"
        protocol = Protocol(id=self._new_id(), title=title, created_at=now)
        version = ProtocolVersion(
            id=self._new_id(),
            protocol_id=protocol.id,
            version_number=1,
            revision=0,
            status=ProtocolVersionStatus.DRAFT,
            steps=steps,
            source_sha256=extracted.sha256,
            created_at=now,
            source_text=extracted.text,
            unassigned_text=parsed.unassigned,
        )
        with self._uow_factory() as uow:
            uow.protocols.add(protocol, version)
            self._append_audit(
                uow,
                session_id=None,
                event_type="protocol_imported",
                correlation_id=self._new_id(),
                details={
                    "protocol_id": protocol.id,
                    "version_id": version.id,
                    "step_count": str(len(steps)),
                    "flag_count": str(sum(len(step.flags) for step in steps)),
                    "unassigned_count": str(len(parsed.unassigned)),
                },
            )
        return ProtocolImportResult(protocol=protocol, version=version)

    @staticmethod
    def _validate_reviewed_steps(
        steps: tuple[ProtocolStepDraft, ...],
    ) -> tuple[ProtocolStepDraft, ...]:
        """Reject a reviewed step list that could not be executed safely.

        Shared by the standalone save and by the atomic review-and-approve path,
        so the two cannot drift into accepting different documents.
        """

        try:
            return validate_protocol_steps(steps)
        except ProtocolValidationError as exc:
            raise ApplicationError(exc.code, f"{exc.field}: {exc}") from exc

    def _review_within(
        self,
        uow: UnitOfWorkPort,
        version_id: str,
        steps: tuple[ProtocolStepDraft, ...],
        *,
        expected_revision: int,
    ) -> ProtocolVersion:
        version = uow.protocols.get_version(version_id)
        if version is None:
            raise ApplicationError(
                "PROTOCOL_VERSION_NOT_FOUND",
                "protocol version was not found",
            )
        if version.revision != expected_revision:
            raise ApplicationError("PROTOCOL_VERSION_CONFLICT", "protocol version changed")
        if version.status is not ProtocolVersionStatus.DRAFT:
            raise ApplicationError("PROTOCOL_NOT_EDITABLE", "approved protocols are immutable")
        reviewed = replace(version, revision=version.revision + 1, steps=steps)
        uow.protocols.save_version(reviewed, expected_revision=expected_revision)
        self._append_audit(
            uow,
            session_id=None,
            event_type="protocol_draft_reviewed",
            correlation_id=self._new_id(),
            details={"version_id": version_id, "revision": str(reviewed.revision)},
        )
        return reviewed

    def update_protocol_draft(
        self,
        version_id: str,
        steps: tuple[ProtocolStepDraft, ...],
        *,
        expected_revision: int,
    ) -> ProtocolVersion:
        """Save reviewed steps without approving them.

        This is what autosave uses. The all-at-once path is
        :meth:`review_approve_and_create_session`.
        """

        steps = self._validate_reviewed_steps(steps)
        with self._uow_factory() as uow:
            return self._review_within(
                uow,
                version_id,
                steps,
                expected_revision=expected_revision,
            )

    def _reviewed_steps_fingerprint(self, steps: tuple[ProtocolStepDraft, ...]) -> str:
        """A stable hash of exactly what the operator asked to approve."""

        return json.dumps(
            [
                {
                    "id": step.id,
                    "number": step.number,
                    "title": step.title,
                    "instruction": step.instruction,
                    "duration_seconds": step.duration_seconds,
                    "safety_notes": list(step.safety_notes),
                    "requirements": [
                        {
                            "requirement_id": requirement.requirement_id,
                            "name": requirement.name,
                            "quantity": (
                                None if requirement.quantity is None else str(requirement.quantity)
                            ),
                            "unit": requirement.unit,
                            "aliases": list(requirement.aliases),
                        }
                        for requirement in step.requirements
                    ],
                    "source": {
                        "page": step.source.page,
                        "section": step.source.section,
                        "excerpt": step.source.excerpt,
                        "ocr_derived": step.source.ocr_derived,
                    },
                    "parallel_allowed": step.parallel_allowed,
                    # Only when present, so a request recorded before flags
                    # existed still replays to the same fingerprint.
                    **(
                        {
                            "flags": [
                                [flag.code, flag.excerpt, flag.acknowledged]
                                for flag in step.flags
                            ]
                        }
                        if step.flags
                        else {}
                    ),
                }
                for step in steps
            ],
            separators=(",", ":"),
            sort_keys=True,
        )

    def review_approve_and_create_session(
        self,
        version_id: str,
        steps: tuple[ProtocolStepDraft, ...],
        *,
        expected_revision: int,
        label: str | None = None,
        idempotency_key: str,
        correlation_id: str,
    ) -> tuple[ProtocolVersion, LabSession]:
        """Save the reviewed steps, approve them, and open the session, atomically.

        Doing this as separate requests had a real failure mode: the reviewed
        revision was saved, the approval call then failed, and the browser was
        left holding a revision number the database had already superseded. Every
        later attempt failed the ``If-Match`` check, and the operator had no way
        to tell what had been persisted.

        One unit of work removes the intermediate state entirely, and the
        idempotency key makes a retry after a lost response report the original
        outcome instead of approving a second time and creating a second session.
        """

        if not idempotency_key.strip():
            raise ApplicationError("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
        steps = self._validate_reviewed_steps(steps)
        normalized_label = self._normalize_session_label(label)
        scope = f"approve:{version_id}"
        request_hash = self._request_hash(
            version_id,
            str(expected_revision),
            self._reviewed_steps_fingerprint(steps),
            normalized_label or "",
        )
        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                if replay.request_sha256 != request_hash:
                    raise ApplicationError(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency key was already used for different input",
                    )
                recorded = uow.protocols.get_version(replay.response["version_id"])
                existing = uow.sessions.get(replay.response["session_id"])
                if recorded is None or existing is None:  # pragma: no cover - FK guarantees
                    raise ApplicationError(
                        "PROTOCOL_VERSION_NOT_FOUND",
                        "the recorded approval could no longer be read back",
                    )
                return recorded, existing

            reviewed = self._review_within(
                uow,
                version_id,
                steps,
                expected_revision=expected_revision,
            )
            approved = self._approve_within(
                uow,
                version_id,
                expected_revision=reviewed.revision,
            )
            session = self._create_session_within(uow, approved, label=normalized_label)
            uow.idempotency.add(
                IdempotencyRecord(
                    scope=scope,
                    key=idempotency_key,
                    request_sha256=request_hash,
                    response={
                        "version_id": approved.id,
                        "revision": str(approved.revision),
                        "session_id": session.id,
                    },
                    created_at=self._clock(),
                )
            )
            self._append_audit(
                uow,
                session_id=session.id,
                event_type="protocol_reviewed_approved_and_started",
                correlation_id=correlation_id,
                details={"version_id": approved.id, "session_id": session.id},
            )
        return approved, session

    # ------------------------------------------------------------------ discovery

    @staticmethod
    def _validate_discovery_range(
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> None:
        SessionOrchestrationService.validate_discovery_range(created_from, created_to)

    def list_protocols(
        self,
        *,
        limit: int = 20,
        query: str | None = None,
        archived: bool = False,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[ProtocolSummary, ...]:
        return self._session_orchestration.list_protocols(
            limit=limit,
            query=query,
            archived=archived,
            created_from=created_from,
            created_to=created_to,
        )

    def list_sessions(
        self,
        *,
        limit: int = 20,
        states: tuple[SessionState, ...] | None = None,
        query: str | None = None,
        protocol_version_id: str | None = None,
        label: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        protocol_archived: bool | None = None,
    ) -> tuple[SessionSummary, ...]:
        return self._session_orchestration.list_sessions(
            limit=limit,
            states=states,
            query=query,
            protocol_version_id=protocol_version_id,
            label=label,
            created_from=created_from,
            created_to=created_to,
            protocol_archived=protocol_archived,
        )

    def get_session_snapshot(self, session_id: str) -> SessionSnapshot:
        return self._session_orchestration.get_session_snapshot(session_id)

    def approve_protocol_and_create_session(
        self,
        version_id: str,
        *,
        expected_revision: int,
    ) -> tuple[ProtocolVersion, LabSession]:
        """Approve a reviewed draft and open its session in one transaction.

        Doing these separately left a window in which a protocol was approved but
        no session existed, and the client had no way to tell which half had
        happened. One unit of work removes the partial-failure state entirely.
        """

        with self._uow_factory() as uow:
            approved = self._approve_within(uow, version_id, expected_revision=expected_revision)
            session = self._create_session_within(uow, approved)
        return approved, session

    # ------------------------------------------------------------------ timers

    def list_timers(self, session_id: str) -> tuple[TimerView, ...]:
        return self._session_orchestration.list_timers(session_id)

    def _settled_timers(self, uow: UnitOfWorkPort, session_id: str) -> tuple[TimerView, ...]:
        return self._session_orchestration.settled_timers(uow, session_id)

    # ------------------------------------------------------------------ gateway

    def invoke_tool(
        self,
        session_id: str,
        tool_name: str,
        parameters: Mapping[str, str],
        *,
        source: EntrySource,
        model_proposed: bool,
        correlation_id: str,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        model_inference_id: str = "",
    ) -> ToolInvocationResult:
        """Run one allowlisted tool against authoritative state.

        Typed commands, UI buttons, and realtime function calls all arrive here.
        The tool registry decides what is legal and what needs a human; nothing
        upstream can widen either. A model-proposed mutation can only ever create
        a proposal for the tools that demand confirmation.
        """

        tool = get_tool(tool_name)
        if tool is None:
            raise ApplicationError("TOOL_NOT_ALLOWLISTED", f"unknown tool: {tool_name}")
        if not correlation_id.strip():
            raise ApplicationError("CORRELATION_ID_REQUIRED", "correlation ID is required")

        scope = f"tool:{session_id}:{tool_name}"
        request_hash = self._request_hash(
            tool_name,
            session_id,
            json.dumps(dict(parameters), separators=(",", ":"), sort_keys=True),
            "" if expected_version is None else str(expected_version),
        )
        with self._uow_factory() as uow:
            if idempotency_key:
                replay = uow.idempotency.get(scope, idempotency_key)
                if replay is not None:
                    if replay.request_sha256 != request_hash:
                        raise ApplicationError(
                            "IDEMPOTENCY_CONFLICT",
                            "idempotency key was already used for different input",
                        )
                    return self._replay_tool(uow, tool, replay, session_id)

            session = self._session(uow, session_id)
            if expected_version is not None:
                self._require_version(session, expected_version)
            if not tool.permits(session.state):
                raise ApplicationError(
                    "TOOL_NOT_LEGAL_IN_STATE",
                    f"{tool.name} is not available while the session is {session.state.value}",
                )

            if tool.kind is ToolKind.READ_ONLY:
                # Read-only tools are validated too. A missing required argument
                # is a caller defect whether or not the call can change state,
                # and silently answering an empty question is worse than saying
                # the request was malformed.
                validated = self._tool_payload(session, tool, parameters)
                message, data = self._read_only_result(uow, session, tool, validated)
                # Reads are audited as well as writes: knowing which passage the
                # operator was shown, and when, is part of the provenance of the
                # decisions they went on to make.
                self._append_audit(
                    uow,
                    session_id=session.id,
                    event_type=tool.audit_event,
                    correlation_id=correlation_id,
                    details={
                        "tool": tool.name,
                        # The instruction text itself is already in the
                        # protocol; repeating it into every audit row would
                        # bloat the trail without adding information.
                        **{
                            key: value
                            for key, value in data.items()
                            if key not in {"instruction", "source_excerpt"}
                        },
                    },
                )
                return ToolInvocationResult(
                    tool=tool.name,
                    committed=False,
                    session=session,
                    pending_action=None,
                    timers=self._settled_timers(uow, session_id),
                    message=message,
                    data=data,
                )

            self._require_no_pending_action(uow, session.id)
            payload = self._tool_payload(session, tool, parameters)
            if tool.name == "complete_step":
                progress = self._active_requirement_progress(uow, session)
                if progress:
                    payload["requirement_progress"] = json.dumps(
                        {item.requirement_id: item.status for item in progress},
                        separators=(",", ":"),
                    )
                    unresolved = [item for item in progress if item.status != "confirmed"]
                    if unresolved:
                        payload["requirement_warning"] = (
                            f"{len(unresolved)} of {len(progress)} visible-label reviews "
                            "are unresolved. Completing the step will record that status."
                        )
            if tool.requires_confirmation(model_proposed=model_proposed):
                pending = self._propose(
                    session,
                    tool,
                    payload,
                    input_source=source,
                    correlation_id=correlation_id,
                    model_inference_id=model_inference_id,
                )
                uow.pending_actions.add(pending)
                self._append_audit(
                    uow,
                    session_id=session.id,
                    event_type="tool_proposed",
                    correlation_id=correlation_id,
                    details={"tool": tool.name},
                )
                result = ToolInvocationResult(
                    tool=tool.name,
                    committed=False,
                    session=session,
                    pending_action=pending,
                    timers=self._settled_timers(uow, session_id),
                    message=f"Proposed {tool.name.replace('_', ' ')}. Confirm to apply it.",
                )
            else:
                updated, entry = self._apply_tool(
                    uow,
                    session,
                    tool,
                    payload,
                    source=source,
                    correlation_id=correlation_id,
                    model_inference_id=model_inference_id,
                )
                result = ToolInvocationResult(
                    tool=tool.name,
                    committed=True,
                    session=updated,
                    pending_action=None,
                    timers=self._settled_timers(uow, session_id),
                    message=self._format_message(tool.success_template, payload),
                    entry=entry,
                )
            if idempotency_key:
                uow.idempotency.add(
                    IdempotencyRecord(
                        scope=scope,
                        key=idempotency_key,
                        request_sha256=request_hash,
                        response={
                            "tool": tool.name,
                            "committed": str(result.committed).lower(),
                            "pending_action_id": (
                                result.pending_action.id
                                if result.pending_action is not None
                                else ""
                            ),
                            "message": result.message,
                        },
                        created_at=self._clock(),
                    )
                )
            return result

    def _replay_tool(
        self,
        uow: UnitOfWorkPort,
        tool: ToolSpec,
        record: IdempotencyRecord,
        session_id: str,
    ) -> ToolInvocationResult:
        """Return the recorded outcome without repeating the effect.

        This is what makes a realtime reconnect safe: resubmitting the same key
        reports what already happened instead of applying it a second time.
        """

        pending_id = record.response.get("pending_action_id", "")
        pending = uow.pending_actions.get(pending_id) if pending_id else None
        return ToolInvocationResult(
            tool=tool.name,
            committed=record.response.get("committed") == "true",
            session=self._session(uow, session_id),
            pending_action=pending,
            timers=self._settled_timers(uow, session_id),
            message=record.response.get("message", "Replayed the previous result."),
            replayed=True,
        )

    def _tool_payload(
        self,
        session: LabSession,
        tool: ToolSpec,
        parameters: Mapping[str, str],
    ) -> dict[str, str]:
        """Validate a tool's arguments deterministically before anything happens."""

        supplied = {key: value for key, value in parameters.items() if value != ""}
        unknown = set(supplied) - set(tool.parameters)
        if unknown:
            raise ApplicationError(
                "TOOL_PARAMETERS_INVALID",
                f"{tool.name} does not accept: {', '.join(sorted(unknown))}",
            )
        missing = set(tool.required_parameters) - set(supplied)
        if missing:
            raise ApplicationError(
                "TOOL_PARAMETERS_INVALID",
                f"{tool.name} requires: {', '.join(sorted(missing))}",
            )
        payload = dict(supplied)
        if tool.name == "record_measurement":
            normalised = validate_and_normalise_command(
                ParsedCommand(intent="RECORD_MEASUREMENT", parameters=payload, confidence=1.0)
            )
            if normalised.intent == "UNSUPPORTED":
                detail = normalised.ambiguities[0] if normalised.ambiguities else "invalid"
                raise ApplicationError("MEASUREMENT_INVALID", detail)
            payload = dict(normalised.parameters)
        if tool.name == "correct_previous_entry":
            normalised = validate_and_normalise_command(
                ParsedCommand(
                    intent="RECORD_MEASUREMENT",
                    parameters={
                        "kind": "measurement",
                        "value": payload["value"],
                        "unit": payload["unit"],
                    },
                    confidence=1.0,
                )
            )
            if normalised.intent == "UNSUPPORTED":
                detail = normalised.ambiguities[0] if normalised.ambiguities else "invalid"
                raise ApplicationError("CORRECTION_INVALID", detail)
            payload["value"] = normalised.parameters["value"]
            payload["unit"] = normalised.parameters["unit"]
        if tool.name == "set_timer":
            normalised = validate_and_normalise_command(
                ParsedCommand(
                    intent="SET_TIMER",
                    parameters={"duration_seconds": payload["duration_seconds"]},
                    confidence=1.0,
                )
            )
            if normalised.intent == "UNSUPPORTED":
                raise ApplicationError("TIMER_DURATION_INVALID", "timer duration is invalid")
            payload["duration_seconds"] = normalised.parameters["duration_seconds"]
            payload.setdefault("label", "timer")
        if tool.name == "skip_step_with_reason":
            reason = " ".join(payload["reason"].split())
            if not reason:
                raise ApplicationError("SKIP_REASON_REQUIRED", "skipping a step requires a reason")
            payload["reason"] = reason
        if session.active_step_id is not None and tool.name in {
            "record_measurement",
            "record_observation",
            "set_timer",
            "complete_step",
            "skip_step_with_reason",
        }:
            payload["step_id"] = session.active_step_id
        return payload

    def _read_only_result(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        tool: ToolSpec,
        parameters: Mapping[str, str],
    ) -> tuple[str, dict[str, str]]:
        """Answer a read-only tool from structured state, never from a model."""

        version = uow.protocols.get_version(session.protocol_version_id)
        if version is None:
            raise ApplicationError("PROTOCOL_VERSION_NOT_FOUND", "protocol version was not found")
        active_index = next(
            (
                index
                for index, step in enumerate(version.steps)
                if step.id == session.active_step_id
            ),
            None,
        )
        current = version.steps[active_index] if active_index is not None else None

        if tool.name == "get_protocol_state":
            completed = sum(
                1
                for step in session.steps
                if step.state in {StepState.COMPLETED, StepState.SKIPPED_WITH_REASON}
            )
            message = (
                f"The session is {session.state.value.casefold()} with "
                f"{completed} of {len(session.steps)} steps finished."
            )
            if current is not None:
                message = (
                    f"You are on step {current.number} of {len(version.steps)}. " + message
                )
            return message, {
                "session_state": session.state.value,
                "completed_steps": str(completed),
                "total_steps": str(len(session.steps)),
            }
        if tool.name in {"get_current_step", "repeat_instruction"}:
            if current is None:
                return "There is no active approved protocol step.", {}
            return (
                f"Step {current.number}: {current.instruction}",
                {
                    "step_id": current.id,
                    "step_number": str(current.number),
                    "instruction": current.instruction,
                    "source_page": str(current.source.page),
                    "source_excerpt": current.source.excerpt,
                },
            )
        if tool.name == "get_next_step":
            next_index = active_index + 1 if active_index is not None else 0
            if next_index >= len(version.steps):
                return "There is no later approved protocol step.", {}
            following = version.steps[next_index]
            return (
                f"Next, step {following.number}: {following.instruction}",
                {
                    "step_id": following.id,
                    "step_number": str(following.number),
                    "instruction": following.instruction,
                },
            )
        if tool.name == "get_active_timers":
            views = [
                view
                for view in self._settled_timers(uow, session.id)
                if view.timer.state in {TimerState.RUNNING, TimerState.PAUSED}
            ]
            if not views:
                return "No timers are running.", {}
            spoken = "; ".join(
                f"{self._timer_name(view, version)}, "
                f"{spoken_duration(max(0, view.remaining_seconds))} left"
                + (" (paused)" if view.timer.state is TimerState.PAUSED else "")
                for view in views
            )
            return f"Active timers: {spoken}.", {"timer_count": str(len(views))}
        if tool.name == "read_last_entry":
            return self._last_entry_result(uow, session, version)
        if tool.name == "acknowledge_timer_alarm":
            sounding = [view for view in self._settled_timers(uow, session.id) if view.alarm_active]
            if not sounding:
                return "No alarm is sounding.", {"acknowledged_count": "0", "timer_ids": ""}
            return "Alarm stopped.", {
                "acknowledged_count": str(len(sounding)),
                "timer_ids": ",".join(view.timer.id for view in sounding),
            }
        if tool.name == "answer_protocol_question":
            question = str(parameters.get("question", ""))
            result = retrieve(version, question)
            answer = compose_answer(result)
            if not result.supported:
                # Abstaining is the correct answer when the document is silent.
                # Filling the gap is the failure mode this whole path exists to
                # prevent.
                return answer, {
                    "grounded": "false",
                    "abstained": "true",
                    "spoken": spoken_answer(result, version),
                }
            best = result.passages[0]
            return answer, {
                "spoken": spoken_answer(result, version),
                "grounded": "true",
                "abstained": "false",
                "chunk_id": best.chunk_id,
                "citation": best.citation,
                "source_page": str(best.page),
                "source_step_id": best.step_id or "",
                "relevance": str(best.score),
            }
        if tool.name == "get_expected_reagents":
            if current is None or not current.requirements:
                return "The active step lists no reagent requirement.", {}
            spoken = ", ".join(
                (
                    f"{requirement.quantity} {requirement.unit} {requirement.name}"
                    if requirement.quantity is not None and requirement.unit is not None
                    else requirement.name
                )
                for requirement in current.requirements
            )
            return (
                f"The active step expects {spoken}. "
                "Label matching does not verify bottle contents or safety.",
                {
                    "requirement_ids": ",".join(
                        requirement.requirement_id for requirement in current.requirements
                    ),
                    "requirement_count": str(len(current.requirements)),
                },
            )
        raise ApplicationError(  # pragma: no cover - registry and branches stay in step
            "TOOL_NOT_IMPLEMENTED",
            f"{tool.name} has no read-only implementation",
        )

    def _apply_tool(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        tool: ToolSpec,
        payload: Mapping[str, str],
        *,
        source: EntrySource,
        correlation_id: str,
        model_inference_id: str,
    ) -> tuple[LabSession, NotebookEntry]:
        """Commit one mutating tool, then append its notebook and audit records."""

        now = self._clock()
        updated = session
        entry_type = "session_action"
        details: dict[str, str] = {"tool": tool.name}

        try:
            if tool.name == "correct_previous_entry":
                return self._apply_correction(
                    uow,
                    session,
                    payload,
                    source=source,
                    correlation_id=correlation_id,
                    at=now,
                )
            if tool.name == "start_session":
                updated = transition_session(session, SessionState.ACTIVE, at=now)
            elif tool.name == "pause_session":
                updated = transition_session(session, SessionState.PAUSED, at=now)
            elif tool.name == "resume_session":
                updated = transition_session(session, SessionState.ACTIVE, at=now)
            elif tool.name == "end_session":
                updated = transition_session(session, SessionState.ABORTED, at=now)
            elif tool.name == "complete_step":
                updated = complete_active_step(session, at=now)
                for key in ("requirement_progress", "requirement_warning"):
                    if key in payload:
                        details[key] = payload[key]
            elif tool.name == "skip_step_with_reason":
                updated = skip_active_step(session, reason=payload["reason"], at=now)
                entry_type = "step_skipped"
                details["reason"] = payload["reason"]
            elif tool.name == "record_measurement":
                entry_type = "measurement"
            elif tool.name == "record_observation":
                entry_type = "observation"
            elif tool.name in {"set_timer", "pause_timer", "resume_timer", "cancel_timer"}:
                entry_type = "timer"
                details.update(
                    self._apply_timer_tool(
                        uow,
                        session,
                        tool,
                        payload,
                        correlation_id=correlation_id,
                        at=now,
                    )
                )
            else:  # pragma: no cover - registry and branches stay in step
                raise ApplicationError("TOOL_NOT_IMPLEMENTED", f"{tool.name} cannot be applied")
        except DomainError as exc:
            raise ApplicationError("INVALID_SESSION_TRANSITION", str(exc)) from exc

        if updated.version != session.version:
            uow.sessions.save(updated, expected_version=session.version)
        entry = NotebookEntry(
            id=self._new_id(),
            session_id=session.id,
            entry_type=entry_type,
            source=source,
            payload={
                "tool": tool.name,
                "intent": tool.intent or tool.name,
                **dict(payload),
                **details,
            },
            created_at=now,
            correlation_id=correlation_id,
            model_inference_id=model_inference_id or None,
        )
        uow.notebook.append(entry)
        self._append_audit(
            uow,
            session_id=session.id,
            event_type=tool.audit_event,
            correlation_id=correlation_id,
            details=details,
        )
        return updated, entry

    def _apply_correction(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        payload: Mapping[str, str],
        *,
        source: EntrySource,
        correlation_id: str,
        at: datetime,
    ) -> tuple[LabSession, NotebookEntry]:
        """Append a linked correction; the corrected entry itself is never changed.

        Fails closed when the target is no longer the newest entry of its chain
        (someone corrected it on screen after the proposal was made), so a
        stale correction can never silently overwrite a newer one.
        """

        target = uow.notebook.get(payload["entry_id"])
        if target is None or target.session_id != session.id:
            raise ApplicationError("NOTEBOOK_ENTRY_NOT_FOUND", "the entry to correct was not found")
        if target.entry_type not in {"measurement", "correction"}:
            raise ApplicationError(
                "CORRECTION_TARGET_INVALID", "only a measurement can be corrected by voice"
            )
        if any(
            entry.supersedes_entry_id == target.id
            for entry in uow.notebook.list_for_session(session.id)
        ):
            raise ApplicationError(
                "CORRECTION_TARGET_CHANGED",
                "that entry was corrected again after this proposal; nothing was changed",
            )
        corrected = target.corrected(
            entry_id=self._new_id(),
            payload={
                "tool": "correct_previous_entry",
                "intent": "CORRECT_PREVIOUS_ENTRY",
                "kind": payload.get("kind") or target.payload.get("kind", "measurement"),
                "value": payload["value"],
                "unit": payload["unit"],
                "old_value": payload.get("old_value") or target.payload.get("value", ""),
                "old_unit": payload.get("old_unit") or target.payload.get("unit", ""),
                "original_entry_id": payload.get("original_entry_id", target.id),
                "reason": "corrected by the operator after read-back",
            },
            source=source,
            correlation_id=correlation_id,
            at=at,
        )
        uow.notebook.append(corrected)
        self._append_audit(
            uow,
            session_id=session.id,
            event_type="notebook_entry_corrected",
            correlation_id=correlation_id,
            details={"entry_id": target.id, "correction_id": corrected.id},
        )
        return session, corrected

    def _last_entry_result(
        self, uow: UnitOfWorkPort, session: LabSession, version: ProtocolVersion
    ) -> tuple[str, dict[str, str]]:
        """Read back the most recent record (not proposals, prompts or audit)."""

        recordable = {"measurement", "correction", "observation", "step_skipped", "timer"}
        entry = next(
            (
                item
                for item in reversed(uow.notebook.list_for_session(session.id))
                if item.entry_type in recordable
                or (item.entry_type == "session_action" and item.payload.get("tool"))
            ),
            None,
        )
        if entry is None:
            return "Nothing has been recorded in this session yet.", {}
        payload = entry.payload
        step_number = self._step_number_for(version, payload.get("step_id"))
        in_step = f" in step {step_number}" if step_number is not None else ""
        if entry.entry_type == "measurement":
            text = (
                f"{payload.get('kind', 'measurement')} {payload.get('value', '')} "
                f"{unit_words(payload.get('unit', ''))}{in_step}"
            )
        elif entry.entry_type == "correction":
            text = (
                f"a correction of {payload.get('kind', 'the measurement')} to "
                f"{payload.get('value', '')} {unit_words(payload.get('unit', ''))}"
            )
        elif entry.entry_type == "observation":
            text = f"an observation{in_step}: {payload.get('observation', '')}"
        elif entry.entry_type == "step_skipped":
            text = f"step {step_number} skipped because {payload.get('reason', '')}"
        else:
            text = payload.get("tool", entry.entry_type).replace("_", " ")
        return f"The last entry was {text}.", {"entry_id": entry.id, "entry_type": entry.entry_type}

    def _active_requirement_progress(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
    ) -> tuple[RequirementProgress, ...]:
        """Read one immutable progress summary for the active completion proposal."""

        version = uow.protocols.get_version(session.protocol_version_id)
        if version is None:
            raise ApplicationError("PROTOCOL_VERSION_NOT_FOUND", "protocol version was not found")
        step = next((item for item in version.steps if item.id == session.active_step_id), None)
        if step is None:
            return ()
        verifications = tuple(
            item
            for item in uow.verifications.list_for_session(session.id)
            if item.step_id == step.id
        )
        return derive_requirement_progress(step.requirements, verifications)

    def _apply_timer_tool(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        tool: ToolSpec,
        payload: Mapping[str, str],
        *,
        correlation_id: str,
        at: datetime,
    ) -> dict[str, str]:
        """Apply a timer transition against the durable timer record."""

        if tool.name == "set_timer":
            timer = start_timer(
                timer_id=self._new_id(),
                session_id=session.id,
                step_id=payload.get("step_id"),
                label=payload.get("label") or "timer",
                duration_seconds=int(payload["duration_seconds"]),
                at=at,
                correlation_id=correlation_id,
            )
            uow.timers.add(timer)
            deadline = timer.deadline_at.isoformat() if timer.deadline_at else ""
            return {"timer_id": timer.id, "deadline_at": deadline}

        timer_id = payload["timer_id"]
        existing = uow.timers.get(timer_id)
        if existing is None or existing.session_id != session.id:
            raise ApplicationError("TIMER_NOT_FOUND", "timer was not found in this session")
        transitions = {
            "pause_timer": pause_timer,
            "resume_timer": resume_timer,
            "cancel_timer": cancel_timer,
        }
        updated = transitions[tool.name](existing, at=at)
        uow.timers.save(updated, expected_version=existing.version)
        return {"timer_id": updated.id, "timer_state": updated.state.value}

    @staticmethod
    def _format_message(template: str, payload: Mapping[str, str]) -> str:
        try:
            return template.format(**payload)
        except (KeyError, IndexError):  # pragma: no cover - templates match their tools
            return template

    def _approve_within(
        self,
        uow: UnitOfWorkPort,
        version_id: str,
        *,
        expected_revision: int,
    ) -> ProtocolVersion:
        version = uow.protocols.get_version(version_id)
        if version is None:
            raise ApplicationError("PROTOCOL_VERSION_NOT_FOUND", "protocol version was not found")
        if version.revision != expected_revision:
            raise ApplicationError("PROTOCOL_VERSION_CONFLICT", "protocol version changed")
        if version.status is not ProtocolVersionStatus.DRAFT:
            raise ApplicationError("PROTOCOL_ALREADY_REVIEWED", "only a draft can be approved")
        try:
            validated_steps = validate_protocol_for_approval(version.steps)
        except ProtocolValidationError as exc:
            raise ApplicationError(exc.code, f"{exc.field}: {exc}") from exc
        approved = replace(
            version,
            revision=version.revision + 1,
            status=ProtocolVersionStatus.APPROVED,
            steps=validated_steps,
            approved_at=self._clock(),
        )
        uow.protocols.save_version(approved, expected_revision=expected_revision)
        self._append_audit(
            uow,
            session_id=None,
            event_type="protocol_approved",
            correlation_id=self._new_id(),
            details={"version_id": version_id},
        )
        return approved

    def _create_session_within(
        self,
        uow: UnitOfWorkPort,
        version: ProtocolVersion,
        *,
        label: str | None = None,
    ) -> LabSession:
        if version.status is not ProtocolVersionStatus.APPROVED:
            raise ApplicationError("PROTOCOL_NOT_APPROVED", "an approved protocol is required")
        session = LabSession(
            id=self._new_id(),
            protocol_version_id=version.id,
            protocol_approved=True,
            state=SessionState.READY,
            steps=tuple(
                SessionStep(step_id=step.id, state=StepState.NOT_STARTED)
                for step in version.steps
            ),
            active_step_id=None,
            version=0,
            created_at=self._clock(),
            label=label,
        )
        uow.sessions.add(session)
        self._append_audit(
            uow,
            session_id=session.id,
            event_type="session_created",
            correlation_id=self._new_id(),
            details={"protocol_version_id": version.id},
        )
        return session

    def record_realtime_grant(
        self,
        session_id: str,
        *,
        provider: str,
        model_name: str,
        transport: str,
        voice: str,
        provider_session_id: str,
        tool_names: tuple[str, ...],
        correlation_id: str,
    ) -> None:
        """Record which realtime model and configuration was attached to a session.

        Provenance names the resolved model identifier rather than an alias, so a
        recorded session stays attributable after the provider's aliases move.
        The grant itself is never stored: it is a credential, and it expires.
        """

        with self._uow_factory() as uow:
            self._session(uow, session_id)
            self._append_audit(
                uow,
                session_id=session_id,
                event_type="realtime_session_granted",
                correlation_id=correlation_id,
                details={
                    "provider": provider,
                    "model_name": model_name,
                    "transport": transport,
                    "voice": voice,
                    "provider_session_id": provider_session_id,
                    "tool_names": ",".join(tool_names),
                },
            )

    def get_protocol(self, protocol_id: str) -> Protocol:
        with self._uow_factory() as uow:
            protocol = uow.protocols.get(protocol_id)
            if protocol is None:
                raise ApplicationError("PROTOCOL_NOT_FOUND", "protocol was not found")
            return protocol

    def get_protocol_source(self, version_id: str) -> ProtocolSource:
        """Return the full extracted source text for side-by-side review."""

        with self._uow_factory() as uow:
            version = uow.protocols.get_version(version_id)
            if version is None:
                raise ApplicationError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            return ProtocolSource(
                version_id=version.id,
                source_sha256=version.source_sha256,
                text=version.source_text,
                character_count=len(version.source_text),
                unassigned_text=version.unassigned_text,
            )

    def get_protocol_version(self, version_id: str) -> ProtocolVersion:
        with self._uow_factory() as uow:
            version = uow.protocols.get_version(version_id)
            if version is None:
                raise ApplicationError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            return version

    def copy_approved_protocol_version(
        self,
        version_id: str,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> ProtocolVersion:
        try:
            return self._protocol_lifecycle.copy_approved_version(
                version_id,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        except ProtocolLifecycleError as exc:
            raise ApplicationError(exc.code, str(exc)) from exc

    def set_protocol_archived(
        self,
        protocol_id: str,
        *,
        archived: bool,
        idempotency_key: str,
        correlation_id: str,
    ) -> Protocol:
        try:
            return self._protocol_lifecycle.set_archived(
                protocol_id,
                archived=archived,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        except ProtocolLifecycleError as exc:
            raise ApplicationError(exc.code, str(exc)) from exc

    def approve_protocol(self, version_id: str, *, expected_revision: int) -> ProtocolVersion:
        with self._uow_factory() as uow:
            return self._approve_within(
                uow,
                version_id,
                expected_revision=expected_revision,
            )

    def create_session(
        self,
        protocol_version_id: str,
        *,
        label: str | None = None,
    ) -> LabSession:
        normalized_label = self._normalize_session_label(label)
        with self._uow_factory() as uow:
            version = uow.protocols.get_version(protocol_version_id)
            if version is None:
                raise ApplicationError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            if version.status is not ProtocolVersionStatus.APPROVED:
                raise ApplicationError("PROTOCOL_NOT_APPROVED", "an approved protocol is required")
            session = LabSession(
                id=self._new_id(),
                protocol_version_id=version.id,
                protocol_approved=True,
                state=SessionState.READY,
                steps=tuple(
                    SessionStep(step_id=step.id, state=StepState.NOT_STARTED)
                    for step in version.steps
                ),
                active_step_id=None,
                version=0,
                created_at=self._clock(),
                label=normalized_label,
            )
            uow.sessions.add(session)
            self._append_audit(
                uow,
                session_id=session.id,
                event_type="session_created",
                correlation_id=self._new_id(),
                details={"protocol_version_id": protocol_version_id},
            )
        return session

    @staticmethod
    def _normalize_session_label(label: str | None) -> str | None:
        normalized = " ".join(label.split()) if label else None
        if normalized and len(normalized) > 120:
            raise ApplicationError(
                "SESSION_LABEL_TOO_LONG",
                "session labels are limited to 120 characters",
            )
        return normalized

    def start_session(self, session_id: str, *, expected_version: int) -> LabSession:
        with self._uow_factory() as uow:
            current = self._session(uow, session_id)
            self._require_version(current, expected_version)
            try:
                active = transition_session(current, SessionState.ACTIVE, at=self._clock())
            except DomainError as exc:
                raise ApplicationError("INVALID_SESSION_TRANSITION", str(exc)) from exc
            uow.sessions.save(active, expected_version=expected_version)
            self._append_audit(
                uow,
                session_id=session_id,
                event_type="session_started",
                correlation_id=self._new_id(),
                details={"version": str(active.version)},
            )
        return active

    def get_session(self, session_id: str) -> LabSession:
        with self._uow_factory() as uow:
            return self._session(uow, session_id)

    def list_notebook(self, session_id: str) -> tuple[NotebookEntry, ...]:
        with self._uow_factory() as uow:
            self._session(uow, session_id)
            return uow.notebook.list_for_session(session_id)

    def get_session_review(self, session_id: str) -> SessionReview:
        with self._uow_factory() as uow:
            session = self._session(uow, session_id)
            return SessionReview(
                session=session,
                entries=uow.notebook.list_for_session(session_id),
                inferences=uow.inferences.list_for_session(session_id),
                verifications=uow.verifications.list_for_session(session_id),
                audit_events=uow.audit.list_for_session(session_id),
                utterances=uow.utterances.list_for_session(session_id),
            )

    def handle_text_command(
        self,
        session_id: str,
        transcript: str,
        *,
        idempotency_key: str,
        expected_version: int,
        correlation_id: str,
    ) -> CommandProposal:
        request_hash = self._request_hash(
            "text",
            session_id,
            transcript,
            str(expected_version),
        )
        try:
            return self._handle_command(
                session_id=session_id,
                transcript=transcript,
                audio=None,
                content_type=None,
                input_source=EntrySource.TYPED_INPUT,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_version=expected_version,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            self._record_marked_inference_failure(
                exc,
                session_id=session_id,
                input_sha256=sha256(transcript.encode("utf-8")).hexdigest(),
                correlation_id=correlation_id,
            )
            raise

    def handle_audio_command(
        self,
        session_id: str,
        audio: bytes,
        *,
        content_type: str,
        idempotency_key: str,
        expected_version: int,
        correlation_id: str,
        require_wake_phrase: bool = False,
        listen_mode: ListenMode = ListenMode.COMMAND,
    ) -> CommandProposal:
        request_hash = self._request_hash(
            "audio",
            session_id,
            sha256(audio).hexdigest(),
            content_type,
            str(expected_version),
            *(("require-wake-phrase",) if require_wake_phrase else ()),
            *(
                (f"listen-mode:{listen_mode.value}",)
                if listen_mode is not ListenMode.COMMAND
                else ()
            ),
        )
        try:
            return self._handle_command(
                session_id=session_id,
                transcript=None,
                audio=audio,
                content_type=content_type,
                input_source=EntrySource.USER_CONFIRMED_SPEECH,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_version=expected_version,
                correlation_id=correlation_id,
                require_wake_phrase=require_wake_phrase,
                listen_mode=listen_mode,
            )
        except Exception as exc:
            self._record_marked_inference_failure(
                exc,
                session_id=session_id,
                input_sha256=sha256(audio).hexdigest(),
                correlation_id=correlation_id,
            )
            raise

    def _handle_command(
        self,
        *,
        session_id: str,
        transcript: str | None,
        audio: bytes | None,
        content_type: str | None,
        input_source: EntrySource,
        idempotency_key: str,
        request_hash: str,
        expected_version: int,
        correlation_id: str,
        require_wake_phrase: bool = False,
        listen_mode: ListenMode = ListenMode.COMMAND,
    ) -> CommandProposal:
        if not idempotency_key.strip():
            raise ApplicationError("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
        if not correlation_id.strip():
            raise ApplicationError("CORRELATION_ID_REQUIRED", "correlation ID is required")
        scope = f"command:{session_id}"
        speech_inference_id = ""
        speech_model_name = ""
        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                if replay.request_sha256 != request_hash:
                    raise ApplicationError(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency key was already used for different input",
                    )
                return self._replay_command(uow, replay, transcript=transcript)

            session = self._session(uow, session_id)
            self._require_version(session, expected_version)
            # READY is interactive for voice so "start the session" can be said;
            # the registry still refuses every mutation that is illegal there.
            if session.state not in {SessionState.READY, SessionState.ACTIVE, SessionState.PAUSED}:
                raise ApplicationError(
                    "SESSION_NOT_INTERACTIVE", "session is not ready, active or paused"
                )
            if audio is None:
                self._require_no_pending_action(uow, session.id)

            if audio is not None:
                if content_type is None:
                    raise ApplicationError("AUDIO_CONTENT_TYPE_REQUIRED", "audio type is required")
                vocabulary = self._speech_vocabulary(uow, session)
                started_at = perf_counter()
                speech_result = self._run_inference(
                    "speech.transcribe",
                    lambda: self._speech.transcribe(
                        audio,
                        content_type=content_type,
                        vocabulary=vocabulary,
                    ),
                )
                speech_latency_ms = max(0, round((perf_counter() - started_at) * 1000))
                transcript = speech_result.transcript
                if require_wake_phrase and not addressed_to_assistant(
                    transcript, self._wake_phrase
                ):
                    # Always-listening mode heard speech that was not addressed
                    # to LabJarvis. Nothing is interpreted, retained, or written:
                    # no transcript, no audio, no notebook entry.
                    return CommandProposal(
                        transcript="",
                        parsed_command=ParsedCommand(
                            intent="WAKE_PHRASE_ABSENT",
                            parameters={},
                            confidence=0.0,
                            ambiguities=("utterance did not start with the wake phrase",),
                        ),
                        pending_action=None,
                        response_text="",
                    )
                # Interface controls ("stop listening", "mute", "stop the alarm")
                # are matched by exact phrase before any interpretation, and never
                # touch the experimental record.
                body = strip_wake_phrase(transcript, self._wake_phrase)
                control = voice_control(body if body is not None else transcript)
                if control is not None and control_allowed(control, listen_mode):
                    return self._handle_voice_control(
                        uow,
                        session,
                        control,
                        transcript=transcript,
                        correlation_id=correlation_id,
                        scope=scope,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                if listen_mode is not ListenMode.COMMAND:
                    # Standby and barge-in segments may only carry their one
                    # control; anything else leaves no trace at all.
                    return CommandProposal(
                        transcript="",
                        parsed_command=ParsedCommand(
                            intent="LISTEN_MODE_IGNORED",
                            parameters={},
                            confidence=0.0,
                            ambiguities=(
                                f"only a control is accepted in {listen_mode.value} mode",
                            ),
                        ),
                        pending_action=None,
                        response_text="",
                    )
                self._require_no_pending_action(uow, session.id)
                retained = self._retain_raw_media(
                    session_id=session_id,
                    modality="audio",
                    content_type=content_type,
                    data=audio,
                )
                speech_inference_id = self._new_id()
                speech_model_name = speech_result.model_name
                uow.inferences.append(
                    ModelInference(
                        id=speech_inference_id,
                        session_id=session_id,
                        modality="speech",
                        adapter=speech_result.adapter,
                        model_name=speech_result.model_name,
                        status="succeeded",
                        confidence=speech_result.confidence,
                        latency_ms=speech_latency_ms,
                        input_sha256=sha256(audio).hexdigest(),
                        output=self._speech_inference_output(
                            transcript,
                            retained,
                            metadata=speech_result.metadata,
                        ),
                        created_at=self._clock(),
                    )
                )
            if transcript is None or not transcript.strip():
                raise ApplicationError("TRANSCRIPT_REQUIRED", "transcript cannot be blank")

            # Consequential spoken input gets a second, more accurate reading.
            # When the two readings disagree about anything that could change the
            # record, the utterance stops here: no command is interpreted and no
            # proposal is created, so a mishearing cannot reach the notebook.
            heard_agreement = ""
            if audio is not None and self._needs_high_integrity_reading(transcript):
                canonical, utterance = self._cross_check_transcript(
                    uow,
                    session_id=session_id,
                    audio=audio,
                    content_type=content_type or "application/octet-stream",
                    realtime_transcript=transcript,
                    primary_model=speech_model_name,
                    primary_inference_id=speech_inference_id,
                    correlation_id=correlation_id,
                )
                if canonical is None:
                    message = self._conflict_message(utterance)
                    uow.notebook.append(
                        NotebookEntry(
                            id=self._new_id(),
                            session_id=session_id,
                            entry_type="transcript_conflict",
                            source=EntrySource.SYSTEM_EVENT,
                            payload={
                                "utterance_id": utterance.id,
                                "agreement": utterance.agreement,
                                "reasons": " | ".join(utterance.reasons),
                                "response_text": message,
                            },
                            created_at=self._clock(),
                            correlation_id=correlation_id,
                        )
                    )
                    uow.idempotency.add(
                        IdempotencyRecord(
                            scope=scope,
                            key=idempotency_key,
                            request_sha256=request_hash,
                            response={
                                "pending_action_id": "",
                                "intent": "TRANSCRIPT_CONFLICT",
                                "transcript": transcript if self._retain_transcripts else "",
                                "confidence": "0.0",
                                "parameters_json": "{}",
                                "response_text": message,
                            },
                            created_at=self._clock(),
                        )
                    )
                    return CommandProposal(
                        transcript=transcript,
                        parsed_command=ParsedCommand(
                            intent="TRANSCRIPT_CONFLICT",
                            parameters={},
                            confidence=0.0,
                            ambiguities=utterance.reasons,
                        ),
                        pending_action=None,
                        response_text=message,
                    )
                transcript = canonical
                heard_agreement = DUAL_AGREED

            context = self._command_context(uow, session)
            started_at = perf_counter()
            text_result = self._run_inference(
                "text.interpret",
                lambda: self._text.interpret(transcript, context),
            )
            parsed = validate_and_normalise_command(text_result.command)
            text_latency_ms = max(0, round((perf_counter() - started_at) * 1000))
            text_inference_id = self._new_id()
            uow.inferences.append(
                ModelInference(
                    id=text_inference_id,
                    session_id=session_id,
                    modality="text",
                    adapter=text_result.adapter,
                    model_name=text_result.model_name,
                    status="succeeded",
                    confidence=parsed.confidence,
                    latency_ms=text_latency_ms,
                    input_sha256=sha256(transcript.encode("utf-8")).hexdigest(),
                    output={
                        "intent": parsed.intent,
                        "ambiguities": " | ".join(parsed.ambiguities),
                        # Adapters record their own prompt/schema identifiers;
                        # the grammar has no prompt.
                        "prompt_version": "not-applicable",
                        **dict(text_result.metadata),
                    },
                    created_at=self._clock(),
                )
            )

            parsed, extras, guidance = self._resolve_references(uow, session, parsed)
            if heard_agreement and parsed.intent == "RECORD_OBSERVATION":
                extras["heard_agreement"] = heard_agreement
            pending: PendingAction | None = None
            refusal: str | None = guidance
            if guidance is None:
                pending, refusal = self._create_pending_action(
                    session,
                    parsed,
                    input_source=input_source,
                    correlation_id=correlation_id,
                    model_inference_id=text_inference_id,
                    extras=extras,
                )
            response_text = guidance or self._grounded_response(
                uow,
                session,
                parsed,
                pending,
                refusal=refusal,
            )
            if pending is not None:
                entry_type = "command_proposal"
            elif refusal is not None or (
                parsed.intent == "UNSUPPORTED" or parsed.confidence < self._command_threshold
            ):
                entry_type = "command_rejected"
            else:
                entry_type = "grounded_guidance"
            uow.notebook.append(
                NotebookEntry(
                    id=self._new_id(),
                    session_id=session_id,
                    entry_type=entry_type,
                    source=EntrySource.MODEL_PROPOSAL,
                    payload={
                        "intent": parsed.intent,
                        "response_text": response_text,
                        **self._transcript_metadata(transcript),
                        **dict(parsed.parameters),
                    },
                    created_at=self._clock(),
                    correlation_id=correlation_id,
                    model_inference_id=text_inference_id,
                )
            )
            if pending is not None:
                uow.pending_actions.add(pending)

            uow.idempotency.add(
                IdempotencyRecord(
                    scope=scope,
                    key=idempotency_key,
                    request_sha256=request_hash,
                    response={
                        "pending_action_id": pending.id if pending is not None else "",
                        "intent": parsed.intent,
                        "transcript": transcript if self._retain_transcripts else "",
                        "confidence": str(parsed.confidence),
                        "parameters_json": json.dumps(
                            dict(parsed.parameters),
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        "response_text": response_text,
                    },
                    created_at=self._clock(),
                )
            )
            self._append_audit(
                uow,
                session_id=session_id,
                event_type=(
                    "command_proposed"
                    if pending is not None
                    else (
                        "command_rejected"
                        if refusal is not None or parsed.intent == "UNSUPPORTED"
                        else "grounded_guidance_returned"
                    )
                ),
                correlation_id=correlation_id,
                details={"intent": parsed.intent},
            )
            return CommandProposal(
                transcript=transcript,
                parsed_command=parsed,
                pending_action=pending,
                response_text=response_text,
            )

    def _replay_command(
        self,
        uow: UnitOfWorkPort,
        record: IdempotencyRecord,
        *,
        transcript: str | None,
    ) -> CommandProposal:
        pending_id = record.response.get("pending_action_id", "")
        pending = uow.pending_actions.get(pending_id) if pending_id else None
        parsed = ParsedCommand(
            intent=record.response["intent"],
            parameters=json.loads(record.response.get("parameters_json", "{}")),
            confidence=float(record.response["confidence"]),
        )
        return CommandProposal(
            transcript=(
                transcript
                or record.response.get("transcript")
                or "[transcript retention disabled]"
            ),
            parsed_command=parsed,
            pending_action=pending,
            response_text=record.response.get(
                "response_text",
                "Replayed the previously processed command.",
            ),
            replayed=True,
        )

    def _propose(
        self,
        session: LabSession,
        tool: ToolSpec,
        payload: Mapping[str, str],
        *,
        input_source: EntrySource,
        correlation_id: str,
        model_inference_id: str,
    ) -> PendingAction:
        """Build one proposal.

        Every proposal in the system is constructed here, whether it came from a
        typed command, a push-to-talk utterance, a realtime function call, or a
        button. Constructing them in two places was how the command path came to
        skip the registry's parameter checks while the gateway applied them.
        """

        return PendingAction(
            id=self._new_id(),
            session_id=session.id,
            intent=tool.intent or tool.name,
            payload=dict(payload),
            source=input_source,
            status=PendingActionStatus.PENDING,
            expected_session_version=session.version,
            created_at=self._clock(),
            correlation_id=correlation_id,
            model_inference_id=model_inference_id,
        )

    def _create_pending_action(
        self,
        session: LabSession,
        parsed: ParsedCommand,
        *,
        input_source: EntrySource,
        correlation_id: str,
        model_inference_id: str,
        extras: Mapping[str, str] | None = None,
    ) -> tuple[PendingAction | None, str | None]:
        """Turn an interpreted command into a proposal, or explain why not.

        The second element is a refusal reason, which the response then quotes.
        An interpretation that cannot become a legal, well-formed tool call is
        refused out loud rather than silently dropped: an operator who hears
        nothing has no way to tell a rejected command from an unheard one.
        """

        if parsed.intent == "UNSUPPORTED" or parsed.confidence < self._command_threshold:
            return None, None
        # The registry is the single allowlist. A model-proposed intent becomes a
        # pending action only when the registry knows it, considers it a mutation
        # that is legal in this session state, and accepts its arguments.
        tool = tool_for_intent(parsed.intent)
        if tool is None or tool.kind is not ToolKind.MUTATING:
            return None, None
        if tool.intent == "VERIFY_REAGENT":
            # A spoken or typed "check reagent" asks for the camera; the label
            # evidence itself only ever arrives through the bounded upload
            # endpoint. Creating a pending action here left a proposal that
            # nothing could satisfy and crashed the response (KeyError, found
            # by the hands-free E2E journey on 2026-09-26).
            return None, None
        if not tool.permits(session.state):
            return None, (
                f"{tool.name.replace('_', ' ')} is not available while the session is "
                f"{session.state.value.lower()}"
            )
        try:
            # Exactly the validation a button press or a realtime call receives.
            payload = self._tool_payload(session, tool, parsed.parameters)
        except ApplicationError as exc:
            return None, str(exc)
        # Resolved context (which timer, the old value of a corrected entry,
        # whether both transcriptions agreed) travels with the proposal so the
        # read-back and the record describe exactly what was confirmed.
        payload.update(extras or {})
        # pragma: no cover - the registry makes this total today
        if not tool.requires_confirmation(model_proposed=True):
            # Every mutating tool requires confirmation of a model proposal.
            # Keeping the check explicit means a future policy change cannot
            # silently commit a spoken command without a human.
            return None, "that action cannot be applied from an interpreted command"
        return (
            self._propose(
                session,
                tool,
                payload,
                input_source=input_source,
                correlation_id=correlation_id,
                model_inference_id=model_inference_id,
            ),
            None,
        )

    #: Words that mark an utterance as consequential even without a numeral.
    _CONSEQUENTIAL_WORDS = frozenset(
        {
            "record",
            "measure",
            "measured",
            "complete",
            "completed",
            "skip",
            "abort",
            "end",
            "stop",
            "ph",
            "temperature",
            "volume",
            "mass",
            "weight",
            "concentration",
            "timer",
        }
    )

    def _needs_high_integrity_reading(self, transcript: str) -> bool:
        """Whether this utterance is worth a second, more accurate reading.

        Escalation is selective on purpose. Re-transcribing "what is the next
        step" would double the latency and cost of the most common interaction
        while protecting nothing: that answer comes from structured state and
        cannot be affected by a misheard word. What does get escalated is
        anything carrying a number, a unit, or a verb that writes to the record.
        """

        if self._verification_speech is None:
            return False
        tokens = salient_tokens(transcript)
        if any(token.replace(".", "", 1).isdigit() for token in tokens):
            return True
        return any(token in self._CONSEQUENTIAL_WORDS for token in tokens)

    def _cross_check_transcript(
        self,
        uow: UnitOfWorkPort,
        *,
        session_id: str,
        audio: bytes,
        content_type: str,
        realtime_transcript: str,
        primary_model: str,
        primary_inference_id: str,
        correlation_id: str,
    ) -> tuple[str | None, Utterance]:
        """Take a second reading and decide, deterministically, what was said.

        Returns the canonical transcript, or ``None`` when the two readings
        conflict. ``None`` means the caller must ask the operator: choosing a
        winner here is exactly how a misheard "50 mL" becomes a notebook entry.
        """

        verifier = self._verification_speech
        if verifier is None:  # pragma: no cover - the caller checks this first
            raise ApplicationError(
                "VERIFICATION_SPEECH_UNAVAILABLE",
                "no high-integrity transcription adapter is configured",
            )
        started_at = perf_counter()
        verification = self._run_inference(
            "speech.verify",
            lambda: verifier.transcribe(
                audio,
                content_type=content_type,
                vocabulary=command_vocabulary(self._wake_phrase),
            ),
        )
        latency_ms = max(0, round((perf_counter() - started_at) * 1000))
        verification_inference_id = self._new_id()
        uow.inferences.append(
            ModelInference(
                id=verification_inference_id,
                session_id=session_id,
                modality="speech_verification",
                adapter=verification.adapter,
                model_name=verification.model_name,
                status="succeeded",
                confidence=verification.confidence,
                latency_ms=latency_ms,
                input_sha256=sha256(audio).hexdigest(),
                output=self._transcript_metadata(verification.transcript),
                created_at=self._clock(),
            )
        )

        comparison = compare_transcripts(realtime_transcript, verification.transcript)
        utterance = Utterance(
            id=self._new_id(),
            session_id=session_id,
            realtime_transcript=(
                realtime_transcript if self._retain_transcripts else ""
            ),
            verification_transcript=(
                verification.transcript if self._retain_transcripts else ""
            ),
            canonical_transcript=(
                (comparison.canonical or "") if self._retain_transcripts else ""
            ),
            agreement=comparison.agreement.value,
            reasons=comparison.reasons,
            primary_model=primary_model,
            verification_model=verification.model_name,
            primary_inference_id=primary_inference_id,
            verification_inference_id=verification_inference_id,
            confirmed_transcript="",
            created_at=self._clock(),
            correlation_id=correlation_id,
            purpose="command",
            # Recording the hash here is what later lets a spoken confirmation
            # refuse the very clip that produced the proposal.
            audio_sha256=sha256(audio).hexdigest(),
        )
        uow.utterances.append(utterance)
        self._append_audit(
            uow,
            session_id=session_id,
            event_type="transcript_cross_checked",
            correlation_id=correlation_id,
            details={
                "agreement": comparison.agreement.value,
                "utterance_id": utterance.id,
                "verification_model": verification.model_name,
            },
        )
        return comparison.canonical, utterance

    @staticmethod
    def _conflict_message(utterance: Utterance) -> str:
        """Explain the disagreement in the operator's terms and ask for a repeat."""

        detail = "; ".join(utterance.reasons) or "the two readings did not match"
        return (
            f"I heard that two different ways ({detail}). "
            "Nothing was recorded. Please say the value and unit again, or type it."
        )

    def _load_resolvable_action(
        self,
        uow: UnitOfWorkPort,
        action_id: str,
    ) -> tuple[PendingAction, LabSession]:
        return self._confirmation.load_resolvable_action(uow, action_id)

    def _resolve_pending_within(
        self,
        uow: UnitOfWorkPort,
        action: PendingAction,
        session: LabSession,
        *,
        accepted: bool,
        correlation_id: str,
        decided_by: str,
    ) -> tuple[LabSession, PendingAction, NotebookEntry | None]:
        return self._confirmation.resolve_pending_within(
            uow,
            action,
            session,
            accepted=accepted,
            correlation_id=correlation_id,
            decided_by=decided_by,
        )

    def confirm_pending_action(
        self,
        action_id: str,
        *,
        accepted: bool,
        correlation_id: str,
    ) -> ConfirmationResult:
        return self._confirmation.confirm_pending_action(
            action_id,
            accepted=accepted,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _pending_expiry_reason(
        session: LabSession,
        action: PendingAction,
    ) -> str | None:
        return ConfirmationService.pending_expiry_reason(session, action)

    @staticmethod
    def _require_no_pending_action(uow: UnitOfWorkPort, session_id: str) -> None:
        if uow.pending_actions.get_pending_for_session(session_id) is not None:
            raise ApplicationError(
                "PENDING_ACTION_EXISTS",
                "resolve the current proposal before starting another action",
            )

    # ------------------------------------------------- spoken confirmation

    def confirm_pending_action_by_voice(
        self,
        action_id: str,
        audio: bytes,
        *,
        content_type: str,
        idempotency_key: str,
        correlation_id: str,
        expected_session_version: int | None = None,
        via_wake_phrase: bool = False,
    ) -> SpokenConfirmationResult:
        try:
            return self._confirm_pending_action_by_voice_transaction(
                action_id,
                audio,
                content_type=content_type,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                expected_session_version=expected_session_version,
                via_wake_phrase=via_wake_phrase,
            )
        except Exception as exc:
            operation = getattr(exc, "labjarvis_operation", None)
            if isinstance(operation, str):
                modality = (
                    "speech_confirmation_verification"
                    if operation == "speech.verify"
                    else "speech_confirmation"
                )
                self._record_marked_inference_failure(
                    exc,
                    session_id=self._pending_action_session_id(action_id),
                    input_sha256=sha256(audio).hexdigest(),
                    correlation_id=correlation_id,
                    modality_override=modality,
                )
            raise

    def _confirm_pending_action_by_voice_transaction(
        self,
        action_id: str,
        audio: bytes,
        *,
        content_type: str,
        idempotency_key: str,
        correlation_id: str,
        expected_session_version: int | None = None,
        via_wake_phrase: bool = False,
    ) -> SpokenConfirmationResult:
        """Answer one proposal with a fresh recording of the operator speaking.

        ``via_wake_phrase`` marks a clip from the always-on listener rather than
        a push-to-talk press (voice-only mode, opt-in). Such a clip must start
        with the wake phrase - otherwise it is ignored without writing anything -
        and must restate the content being confirmed (see
        :mod:`labjarvis.domain.voice_confirmation`); a bare "yes" never commits.

        This is what makes the hands-free path complete without handing the
        realtime model any authority. Three properties do the work:

        * The evidence is **a client-captured microphone recording**, which the
          realtime model cannot supply through the channel it controls: it can
          ask the operator to confirm, but it cannot put audio into this
          endpoint. Note what that does *not* establish - the backend cannot
          authenticate the client, so this is not proof of human liveness. It is
          proportionate for a loopback, single-trusted-operator deployment,
          where the control exists to stop a model acting unilaterally rather
          than to stop the operator deceiving their own notebook.
        * The recording must be **new**. A confirmation whose audio hash matches
          any utterance already stored for this session is refused, so the clip
          that produced the proposal cannot be resubmitted as its own approval.
          Server-side content identity is used rather than a client timestamp,
          which the browser could simply assert. Exact hashing stops a
          byte-identical resubmission and nothing more: a re-encoded copy, or a
          different recording of the same words, has a different hash. The
          browser is what keeps the assistant's own readback out of the clip, by
          refusing to record until the readback has demonstrably finished.
        * The wording is read by the **high-integrity** transcription path and
          classified deterministically. For a measurement, the operator has to
          say the value back; "yes" alone is not accepted, because it proves
          they replied and not that they heard the same number.

        Anything short of an unambiguous answer leaves the proposal pending and
        asks again. Nothing is ever resolved by guessing.
        """

        if not idempotency_key.strip():
            raise ApplicationError("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
        if not correlation_id.strip():
            raise ApplicationError("CORRELATION_ID_REQUIRED", "correlation ID is required")
        if not audio:
            raise ApplicationError("AUDIO_REQUIRED", "a confirmation recording is required")

        audio_hash = sha256(audio).hexdigest()
        scope = f"spoken-confirm:{action_id}"
        request_hash = self._request_hash(action_id, audio_hash, content_type)

        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                if replay.request_sha256 != request_hash:
                    raise ApplicationError(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency key was already used for different input",
                    )
                return self._replay_spoken_confirmation(uow, replay, action_id)

            action, session = self._load_resolvable_action(uow, action_id)
            if (
                expected_session_version is not None
                and session.version != expected_session_version
            ):
                raise ApplicationError(
                    "SESSION_VERSION_CONFLICT",
                    "the session changed after this proposal was read out",
                )

            # A confirmation has to be its own recording. Reusing the clip that
            # produced the proposal would let one utterance both request and
            # approve a change to the record.
            if uow.utterances.audio_already_used(session.id, audio_hash):
                raise ApplicationError(
                    "CONFIRMATION_AUDIO_REPLAYED",
                    "that recording was already used in this session; speak again",
                )

            if via_wake_phrase and not self._voice_only_confirmation:
                raise ApplicationError(
                    "VOICE_ONLY_CONFIRMATION_DISABLED",
                    "voice-only confirmation is switched off; use push-to-talk or the screen",
                )
            requires_readback = action.intent in READBACK_REQUIRED_INTENTS or via_wake_phrase
            if requires_readback and self._verification_speech is None:
                # Refusing is the honest outcome: the integrity control this
                # decision depends on is not configured, so the operator uses
                # the on-screen confirmation instead.
                raise ApplicationError(
                    "HIGH_INTEGRITY_TRANSCRIPTION_UNAVAILABLE",
                    "spoken confirmation of a measurement needs the high-integrity "
                    "transcription adapter; confirm on screen instead",
                )

            precomputed: tuple[Any, int] | None = None
            if via_wake_phrase:
                started_at = perf_counter()
                heard = self._run_inference(
                    "speech.transcribe",
                    lambda: self._speech.transcribe(audio, content_type=content_type),
                )
                if not addressed_to_assistant(heard.transcript, self._wake_phrase):
                    # The listener heard speech not addressed to LabJarvis (a
                    # colleague, a radio, an echo of the prompt, which never
                    # contains the wake phrase). Nothing is retained or written
                    # and the proposal keeps waiting.
                    return SpokenConfirmationResult(
                        decision=VoiceDecision.NOT_ADDRESSED.value,
                        committed=False,
                        session=session,
                        pending_action=action,
                        entry=None,
                        response_text="",
                        utterance_id="",
                        heard="",
                        reasons=("the reply did not start with the wake phrase",),
                    )
                heard_body = strip_wake_phrase(heard.transcript, self._wake_phrase) or ""
                control = voice_control(heard_body)
                if control is not None:
                    # An interface control ("stop", "mute", "stop the alarm")
                    # said while a proposal waits: act on it, leave the proposal
                    # pending, write nothing but an audit event.
                    response = CONTROL_RESPONSES[control]
                    if control is VoiceControl.ACKNOWLEDGE_ALARM:
                        response = self._acknowledge_alarms(
                            uow, session, correlation_id=correlation_id
                        )
                    self._append_audit(
                        uow,
                        session_id=session.id,
                        event_type="voice_control",
                        correlation_id=correlation_id,
                        details={"control": control.value, "action_id": action.id},
                    )
                    return SpokenConfirmationResult(
                        decision="control",
                        committed=False,
                        session=session,
                        pending_action=action,
                        entry=None,
                        response_text=response,
                        utterance_id="",
                        heard="",
                        control=control.value,
                    )
                precomputed = (heard, max(0, round((perf_counter() - started_at) * 1000)))
            primary, primary_inference_id = self._transcribe_for_confirmation(
                uow,
                session_id=session.id,
                audio=audio,
                content_type=content_type,
                modality="speech_confirmation",
                precomputed=precomputed,
            )
            canonical = primary.transcript
            agreement = "unavailable"
            reasons: tuple[str, ...] = ()
            verification_transcript = ""
            verification_model = ""
            verification_inference_id = ""

            if self._verification_speech is not None:
                verification, verification_inference_id = self._transcribe_for_confirmation(
                    uow,
                    session_id=session.id,
                    audio=audio,
                    content_type=content_type,
                    modality="speech_confirmation_verification",
                    verifier=True,
                )
                verification_transcript = verification.transcript
                verification_model = verification.model_name
                comparison = compare_transcripts(primary.transcript, verification.transcript)
                agreement = comparison.agreement.value
                reasons = comparison.reasons
                canonical = comparison.canonical or ""

            reading = read_confirmation(canonical)
            decision = reading.intent.value
            outcome_reasons = list(reasons)

            voice_expected = (
                self._voice_expected(uow, session, action) if via_wake_phrase else None
            )
            if canonical == "":
                decision = ConfirmationIntent.AMBIGUOUS.value
                outcome_reasons.append("the two readings of your reply did not match")
            elif via_wake_phrase:
                voice_verdict = judge_voice_only_confirmation(
                    canonical, wake_phrase=self._wake_phrase, expected=voice_expected
                )
                decision = {
                    VoiceDecision.CONFIRM: ConfirmationIntent.CONFIRM.value,
                    VoiceDecision.REJECT: ConfirmationIntent.REJECT.value,
                }.get(voice_verdict.decision, ConfirmationIntent.AMBIGUOUS.value)
                outcome_reasons.extend(voice_verdict.reasons)
            elif reading.intent is ConfirmationIntent.CONFIRM and requires_readback:
                verdict = check_measurement_readback(
                    reading,
                    expected_value=action.payload.get("value", ""),
                    expected_unit=action.payload.get("unit", ""),
                )
                if not verdict.accepted:
                    decision = ConfirmationIntent.AMBIGUOUS.value
                    outcome_reasons.extend(verdict.reasons)

            utterance = Utterance(
                id=self._new_id(),
                session_id=session.id,
                realtime_transcript=(
                    primary.transcript if self._retain_transcripts else ""
                ),
                verification_transcript=(
                    verification_transcript if self._retain_transcripts else ""
                ),
                canonical_transcript=(canonical if self._retain_transcripts else ""),
                agreement=agreement,
                reasons=tuple(reasons),
                primary_model=primary.model_name,
                verification_model=verification_model,
                primary_inference_id=primary_inference_id,
                verification_inference_id=verification_inference_id,
                confirmed_transcript=(canonical if self._retain_transcripts else ""),
                created_at=self._clock(),
                correlation_id=correlation_id,
                purpose="confirmation",
                pending_action_id=action.id,
                audio_sha256=audio_hash,
                decision=decision,
            )
            uow.utterances.append(utterance)

            committed = False
            entry: NotebookEntry | None = None
            resolved = action
            updated_session = session

            if decision == ConfirmationIntent.CONFIRM.value:
                updated_session, resolved, entry = self._resolve_pending_within(
                    uow,
                    action,
                    session,
                    accepted=True,
                    correlation_id=correlation_id,
                    decided_by="spoken_confirmation",
                )
                committed = True
                response_text = self._committed_text(uow, session, action)
            elif decision == ConfirmationIntent.REJECT.value:
                updated_session, resolved, entry = self._resolve_pending_within(
                    uow,
                    action,
                    session,
                    accepted=False,
                    correlation_id=correlation_id,
                    decided_by="spoken_rejection",
                )
                response_text = "Rejected. Nothing was recorded."
            else:
                detail = "; ".join(outcome_reasons) or "the reply was not clear"
                # Spoken prompts never contain the wake phrase itself, so an
                # echo of this read-back can never pass the wake-phrase check.
                prompt = (
                    f"Say the wake word, then: {voice_expected.phrase()}"
                    if via_wake_phrase and voice_expected is not None
                    else (
                        "Confirm this one with push-to-talk or on screen"
                        if via_wake_phrase
                        else readback_prompt(action.intent, dict(action.payload))
                    )
                )
                response_text = (
                    f"I could not act on that ({detail}). Nothing was recorded. {prompt}."
                )
                self._append_audit(
                    uow,
                    session_id=session.id,
                    event_type="spoken_confirmation_ambiguous",
                    correlation_id=correlation_id,
                    details={
                        "action_id": action.id,
                        "intent": action.intent,
                        "utterance_id": utterance.id,
                        "agreement": agreement,
                    },
                )

            uow.notebook.append(
                NotebookEntry(
                    id=self._new_id(),
                    session_id=session.id,
                    entry_type=f"spoken_confirmation_{decision}",
                    source=EntrySource.USER_CONFIRMED_SPEECH,
                    payload={
                        "action_id": action.id,
                        "intent": action.intent,
                        "utterance_id": utterance.id,
                        "agreement": agreement,
                        "decision": decision,
                        "response_text": response_text,
                        **self._transcript_metadata(canonical),
                    },
                    created_at=self._clock(),
                    correlation_id=correlation_id,
                    model_inference_id=primary_inference_id,
                )
            )
            uow.idempotency.add(
                IdempotencyRecord(
                    scope=scope,
                    key=idempotency_key,
                    request_sha256=request_hash,
                    response={
                        "decision": decision,
                        "committed": str(committed).lower(),
                        "response_text": response_text,
                        "utterance_id": utterance.id,
                        "heard": canonical if self._retain_transcripts else "",
                        "entry_id": entry.id if entry is not None else "",
                    },
                    created_at=self._clock(),
                )
            )

        return SpokenConfirmationResult(
            decision=decision,
            committed=committed,
            session=updated_session,
            pending_action=resolved,
            entry=entry,
            response_text=response_text,
            utterance_id=utterance.id,
            heard=canonical if self._retain_transcripts else "",
            reasons=tuple(outcome_reasons),
        )

    def _replay_spoken_confirmation(
        self,
        uow: UnitOfWorkPort,
        record: IdempotencyRecord,
        action_id: str,
    ) -> SpokenConfirmationResult:
        """Report what the first delivery of this confirmation did."""

        action = uow.pending_actions.get(action_id)
        if action is None:  # pragma: no cover - the record proves it existed
            raise ApplicationError("PENDING_ACTION_NOT_FOUND", "pending action was not found")
        return SpokenConfirmationResult(
            decision=record.response.get("decision", "ambiguous"),
            committed=record.response.get("committed") == "true",
            session=self._session(uow, action.session_id),
            pending_action=action,
            entry=None,
            response_text=record.response.get("response_text", "Already handled."),
            utterance_id=record.response.get("utterance_id", ""),
            heard=record.response.get("heard", ""),
            replayed=True,
        )

    @staticmethod
    def _active_step_number(uow: UnitOfWorkPort, session: LabSession) -> int | None:
        version = uow.protocols.get_version(session.protocol_version_id)
        if version is None:
            return None
        for step in version.steps:
            if step.id == session.active_step_id:
                return step.number
        return None

    def _transcribe_for_confirmation(
        self,
        uow: UnitOfWorkPort,
        *,
        session_id: str,
        audio: bytes,
        content_type: str,
        modality: str,
        verifier: bool = False,
        precomputed: tuple[Any, int] | None = None,
    ) -> tuple[Any, str]:
        """Transcribe a confirmation clip and record the call as provenance.

        ``precomputed`` is a (result, latency) pair already obtained - the
        wake-phrase check transcribes first, so that unaddressed speech leaves
        no trace - and is recorded here without transcribing twice.
        """

        adapter = self._verification_speech if verifier else self._speech
        if adapter is None:  # pragma: no cover - callers check first
            raise ApplicationError(
                "VERIFICATION_SPEECH_UNAVAILABLE",
                "no high-integrity transcription adapter is configured",
            )
        if precomputed is not None:
            result, latency_ms = precomputed
        else:
            started_at = perf_counter()
            result = self._run_inference(
                "speech.verify" if verifier else "speech.transcribe",
                lambda: adapter.transcribe(
                    audio,
                    content_type=content_type,
                    vocabulary=command_vocabulary(self._wake_phrase) if verifier else (),
                ),
            )
            latency_ms = max(0, round((perf_counter() - started_at) * 1000))
        inference_id = self._new_id()
        uow.inferences.append(
            ModelInference(
                id=inference_id,
                session_id=session_id,
                modality=modality,
                adapter=result.adapter,
                model_name=result.model_name,
                status="succeeded",
                confidence=result.confidence,
                latency_ms=latency_ms,
                input_sha256=sha256(audio).hexdigest(),
                output=self._transcript_metadata(result.transcript),
                created_at=self._clock(),
            )
        )
        return result, inference_id

    def _apply_confirmed_action(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        action: PendingAction,
        *,
        correlation_id: str,
    ) -> tuple[LabSession, NotebookEntry]:
        """Apply a confirmed proposal through the same gateway a button uses.

        Confirmation does not get its own execution path. Resolving the intent
        back to its tool means a proposal and a direct action are validated and
        recorded identically, so a model-originated change can never take a
        shortcut a human action would not have.
        """

        tool = tool_for_intent(action.intent)
        if tool is None or tool.kind is not ToolKind.MUTATING:
            raise ApplicationError("UNSUPPORTED_ACTION", "pending action is not allowlisted")
        if not tool.permits(session.state):
            raise ApplicationError(
                "TOOL_NOT_LEGAL_IN_STATE",
                f"{tool.name} is not available while the session is {session.state.value}",
            )
        return self._apply_tool(
            uow,
            session,
            tool,
            action.payload,
            source=action.source,
            correlation_id=correlation_id,
            model_inference_id=action.model_inference_id,
        )

    # ------------------------------------------------------- hands-free helpers

    def _handle_voice_control(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        control: VoiceControl,
        *,
        transcript: str,
        correlation_id: str,
        scope: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CommandProposal:
        """Answer an interface control. Nothing in the experimental record changes.

        Only the audit trail records that a control was used (never the audio);
        silencing an alarm is the one control with a durable effect, and that
        effect is itself only an audit event.
        """

        response_text = CONTROL_RESPONSES[control]
        if control is VoiceControl.ACKNOWLEDGE_ALARM:
            response_text = self._acknowledge_alarms(uow, session, correlation_id=correlation_id)
        self._append_audit(
            uow,
            session_id=session.id,
            event_type="voice_control",
            correlation_id=correlation_id,
            details={"control": control.value},
        )
        uow.idempotency.add(
            IdempotencyRecord(
                scope=scope,
                key=idempotency_key,
                request_sha256=request_hash,
                response={
                    "pending_action_id": "",
                    "intent": control.value,
                    "transcript": transcript if self._retain_transcripts else "",
                    "confidence": "0.99",
                    "parameters_json": "{}",
                    "response_text": response_text,
                },
                created_at=self._clock(),
            )
        )
        return CommandProposal(
            transcript=transcript if self._retain_transcripts else "",
            parsed_command=ParsedCommand(intent=control.value, parameters={}, confidence=0.99),
            pending_action=None,
            response_text=response_text,
        )

    def _acknowledge_alarms(
        self, uow: UnitOfWorkPort, session: LabSession, *, correlation_id: str
    ) -> str:
        """Silence every sounding alarm by appending one acknowledgement event."""

        sounding = [view for view in self._settled_timers(uow, session.id) if view.alarm_active]
        if not sounding:
            return "No alarm is sounding."
        self._append_audit(
            uow,
            session_id=session.id,
            event_type=ALARM_ACKNOWLEDGED_EVENT,
            correlation_id=correlation_id,
            details={
                "tool": "acknowledge_timer_alarm",
                "timer_ids": ",".join(view.timer.id for view in sounding),
                "acknowledged_count": str(len(sounding)),
            },
        )
        return "Alarm stopped."

    def _step_number_for(self, version: ProtocolVersion | None, step_id: str | None) -> int | None:
        if version is None or step_id is None:
            return None
        return next((step.number for step in version.steps if step.id == step_id), None)

    def _timer_name(self, view: TimerView, version: ProtocolVersion | None) -> str:
        """How a timer is spoken: "the incubation timer" or "the timer for step 3"."""

        timer = view.timer
        duration = adjectival_duration(timer.duration_seconds)
        label = " ".join(timer.label.split())
        step_number = self._step_number_for(version, timer.step_id)
        if label and label.casefold() not in {"timer", "a timer"}:
            name = f"the {duration} {label} timer"
        else:
            name = f"the {duration} timer"
        if step_number is not None:
            name += f" for step {step_number}"
        return name

    def _resolve_references(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        parsed: ParsedCommand,
    ) -> tuple[ParsedCommand, dict[str, str], str | None]:
        """Resolve a spoken reference against authoritative state.

        Returns the command with concrete identifiers, context to carry on the
        proposal, and - when the reference is missing, unknown or ambiguous - a
        spoken question or explanation instead of a proposal. A reference is
        never resolved by picking a default silently.
        """

        extras: dict[str, str] = {}
        if parsed.confidence < self._command_threshold:
            return parsed, extras, None
        version = uow.protocols.get_version(session.protocol_version_id)
        intent = parsed.intent
        params = dict(parsed.parameters)

        if intent == "SKIP_STEP_WITH_REASON" and not params.get("reason"):
            return parsed, extras, (
                "Say why the step is being skipped, for example: skip this step "
                "because the reagent is unavailable. Nothing was recorded."
            )

        if intent == "CORRECT_PREVIOUS_ENTRY":
            if not params:
                return parsed, extras, (
                    "Say the new value, for example: correct the last measurement to "
                    "12 millilitres. Nothing was changed."
                )
            head, original = self._correction_target(uow, session, params.get("target", ""))
            if head is None or original is None:
                named = params.get("target") or ""
                return parsed, extras, (
                    f"There is no recorded {named + ' ' if named else ''}measurement in "
                    "this session to correct. Nothing was changed."
                )
            old_value = head.payload.get("value", "")
            old_unit = head.payload.get("unit", "")
            if old_value == params["value"] and old_unit == params["unit"]:
                return parsed, extras, (
                    f"{params['value']} {unit_words(params['unit'])} is already the recorded "
                    "value. Nothing was changed."
                )
            extras.update(
                {
                    "kind": head.payload.get("kind") or original.payload.get("kind", "measurement"),
                    "old_value": old_value,
                    "old_unit": old_unit,
                    "original_entry_id": original.id,
                }
            )
            resolved = ParsedCommand(
                intent=intent,
                parameters={"entry_id": head.id, "value": params["value"], "unit": params["unit"]},
                confidence=parsed.confidence,
            )
            return resolved, extras, None

        if intent in {"PAUSE_TIMER", "RESUME_TIMER", "CANCEL_TIMER"}:
            states = {
                "PAUSE_TIMER": {TimerState.RUNNING},
                "RESUME_TIMER": {TimerState.PAUSED},
                "CANCEL_TIMER": {TimerState.RUNNING, TimerState.PAUSED},
            }[intent]
            views = [
                view
                for view in self._settled_timers(uow, session.id)
                if view.timer.state in states
            ]
            verb = _TIMER_VERBS[intent]
            if not views:
                kind = "paused" if intent == "RESUME_TIMER" else "running"
                return parsed, extras, f"There is no {kind} timer to {verb}. Nothing was changed."
            matches = _match_timers(views, params.get("timer", ""), version)
            if not matches:
                names = "; ".join(self._timer_name(view, version) for view in views)
                return parsed, extras, (
                    f"No timer matches {params.get('timer', 'that')}. Timers: {names}. "
                    "Nothing was changed."
                )
            if len(matches) > 1:
                names = "; or ".join(self._timer_name(view, version) for view in matches)
                return parsed, extras, (
                    f"Which timer should I {verb}? {names}. Say it again with the duration "
                    "or the step. Nothing was changed."
                )
            chosen = matches[0]
            extras.update(
                {
                    "timer_label": chosen.timer.label,
                    "timer_duration_seconds": str(chosen.timer.duration_seconds),
                    "timer_name": self._timer_name(chosen, version),
                }
            )
            return (
                ParsedCommand(
                    intent=intent,
                    parameters={"timer_id": chosen.timer.id},
                    confidence=parsed.confidence,
                ),
                extras,
                None,
            )

        if intent == "VERIFY_REAGENT":
            step = (
                next((item for item in version.steps if item.id == session.active_step_id), None)
                if version is not None
                else None
            )
            requirements = step.requirements if step is not None else ()
            if not requirements:
                return parsed, extras, "The current step has no reagent requirement to check."
            listing = "; ".join(
                f"{index}, {requirement.name}" for index, requirement in enumerate(requirements, 1)
            )
            reference = params.get("reagent", "")
            if not reference:
                if len(requirements) > 1:
                    return parsed, extras, (
                        f"Which reagent? This step expects: {listing}. Say check, then the "
                        "name or the number."
                    )
                chosen_requirement = requirements[0]
            else:
                found = _match_requirement(requirements, reference)
                if found is None:
                    return parsed, extras, (
                        f"I don't know {reference} in this step. This step expects: {listing}."
                    )
                chosen_requirement = found
            return (
                ParsedCommand(
                    intent=intent,
                    parameters={
                        "requirement_id": chosen_requirement.requirement_id,
                        "reagent_name": chosen_requirement.name,
                    },
                    confidence=parsed.confidence,
                ),
                extras,
                None,
            )
        return parsed, extras, None

    @staticmethod
    def _correction_target(
        uow: UnitOfWorkPort, session: LabSession, target: str
    ) -> tuple[NotebookEntry | None, NotebookEntry | None]:
        """The newest entry of the most recent (or named) measurement chain.

        Returns ``(head, original)``: the entry a new correction must supersede
        (the measurement itself, or its latest correction) and the original
        measurement. Corrections are chained, never overwritten.
        """

        entries = uow.notebook.list_for_session(session.id)
        by_id = {entry.id: entry for entry in entries}
        superseded = {entry.supersedes_entry_id for entry in entries if entry.supersedes_entry_id}

        def root(entry: NotebookEntry) -> NotebookEntry:
            seen: set[str] = set()
            while entry.supersedes_entry_id and entry.supersedes_entry_id in by_id:
                if entry.id in seen:  # pragma: no cover - chains are acyclic
                    break
                seen.add(entry.id)
                entry = by_id[entry.supersedes_entry_id]
            return entry

        wanted = set(target.casefold().split())
        for entry in reversed(entries):
            if entry.id in superseded:
                continue
            original = root(entry)
            if original.entry_type != "measurement":
                continue
            kind = (entry.payload.get("kind") or original.payload.get("kind", "")).casefold()
            if wanted and not wanted <= set(kind.split()):
                continue
            return entry, original
        return None, None

    def _proposal_text(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        pending: PendingAction,
    ) -> str:
        """A short spoken description of a proposal plus the phrase to confirm it.

        The phrase never contains the wake phrase itself ("say the wake word,
        then: ..."), so an echo of this prompt cannot address LabJarvis.
        """

        payload = dict(pending.payload)
        version = uow.protocols.get_version(session.protocol_version_id)
        step_number = self._step_number_for(version, session.active_step_id)
        in_step = f" in step {step_number}" if step_number is not None else ""
        intent = pending.intent
        if intent == "RECORD_MEASUREMENT":
            what = (
                f"Proposed: {payload.get('kind', 'measurement')} "
                f"{payload.get('value', '')} {unit_words(payload.get('unit', ''))}{in_step}."
            )
        elif intent == "CORRECT_PREVIOUS_ENTRY":
            what = (
                f"Proposed correction: {payload.get('kind', 'measurement')} from "
                f"{payload.get('old_value', '')} {unit_words(payload.get('old_unit', ''))} to "
                f"{payload.get('value', '')} {unit_words(payload.get('unit', ''))}. "
                "The original entry is kept."
            )
        elif intent == "RECORD_OBSERVATION":
            what = f"I heard: {payload.get('observation', '')}."
        elif intent == "SET_TIMER":
            seconds = int(payload.get("duration_seconds", "0") or 0)
            what = f"Proposed a {spoken_duration(seconds)} timer{in_step}."
        elif intent == "COMPLETE_STEP":
            what = f"Proposed completing step {step_number}." if step_number else (
                "Proposed completing the current step."
            )
            if payload.get("requirement_warning"):
                what += f" {payload['requirement_warning']}"
        elif intent == "SKIP_STEP_WITH_REASON":
            what = f"Proposed skipping step {step_number} because {payload.get('reason', '')}."
        elif intent in {"PAUSE_TIMER", "RESUME_TIMER", "CANCEL_TIMER"}:
            what = (
                f"Proposed {_TIMER_GERUNDS[intent]} "
                f"{payload.get('timer_name', 'the timer')}."
            )
        else:
            what = {
                "PAUSE_SESSION": "Proposed pausing the session.",
                "RESUME_SESSION": "Proposed resuming the session.",
                "END_SESSION": "Proposed ending the session early.",
                "START_SESSION": "Proposed starting the session.",
            }.get(intent, f"Proposed {intent.replace('_', ' ').casefold()}.")
        text = f"{what} Review and confirm before it is applied."
        if self._voice_only_confirmation:
            expected = self._voice_expected(uow, session, pending)
            if expected is not None:
                text += f" To confirm, say the wake word, then: {expected.phrase()}. Or reject."
            else:
                text += " This one is confirmed on screen or with push-to-talk."
        return text

    def _voice_expected(
        self, uow: UnitOfWorkPort, session: LabSession, action: PendingAction
    ) -> ExpectedReadback | None:
        return expected_readback(
            action.intent,
            dict(action.payload),
            step_number=self._active_step_number(uow, session),
            voice_observations=self._voice_observations,
        )

    def _committed_text(
        self, uow: UnitOfWorkPort, session: LabSession, action: PendingAction
    ) -> str:
        """What LabJarvis says after a proposal is committed, from the payload."""

        payload = dict(action.payload)
        version = uow.protocols.get_version(session.protocol_version_id)
        step_number = self._step_number_for(
            version, payload.get("step_id") or session.active_step_id
        )
        in_step = f" in step {step_number}" if step_number is not None else ""
        intent = action.intent
        if intent == "RECORD_MEASUREMENT":
            return (
                f"Recorded {payload.get('value', '')} {unit_words(payload.get('unit', ''))} "
                f"for {payload.get('kind', 'the measurement')}{in_step}."
            )
        if intent == "CORRECT_PREVIOUS_ENTRY":
            return (
                f"Corrected {payload.get('kind', 'the measurement')} to {payload.get('value', '')} "
                f"{unit_words(payload.get('unit', ''))}. The original entry is kept."
            )
        if intent == "RECORD_OBSERVATION":
            return f"Recorded the observation{in_step}."
        if intent == "SKIP_STEP_WITH_REASON":
            return f"Skipped step {step_number}. The reason is recorded." if step_number else (
                "Skipped the step. The reason is recorded."
            )
        if intent == "COMPLETE_STEP":
            return f"Step {step_number} complete." if step_number else "Step complete."
        if intent == "SET_TIMER":
            seconds = int(payload.get("duration_seconds", "0") or 0)
            return f"Started a {spoken_duration(seconds)} timer."
        if intent in {"PAUSE_TIMER", "RESUME_TIMER", "CANCEL_TIMER"}:
            verb = {"PAUSE_TIMER": "Paused", "RESUME_TIMER": "Resumed", "CANCEL_TIMER": "Cancelled"}
            return f"{verb[intent]} {payload.get('timer_name', 'the timer')}."
        tool = tool_for_intent(intent)
        return (
            self._format_message(tool.success_template, payload)
            if tool is not None
            else "Recorded."
        )

    def _grounded_response(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        parsed: ParsedCommand,
        pending: PendingAction | None,
        *,
        refusal: str | None = None,
    ) -> str:
        if parsed.intent == "UNSUPPORTED" or parsed.confidence < self._command_threshold:
            detail = parsed.ambiguities[0] if parsed.ambiguities else "low confidence"
            return f"I could not map that to one supported action ({detail}). No change was made."
        if refusal is not None:
            # The registry refused the call. Saying so, in the registry's own
            # words, is more useful than a generic apology.
            return f"I could not do that: {refusal}. Nothing was recorded."
        if pending is not None:
            return self._proposal_text(uow, session, pending)
        read_tool = tool_for_intent(parsed.intent)
        if (
            read_tool is not None
            and read_tool.kind is ToolKind.READ_ONLY
            and parsed.intent
            in {
                "GET_PROTOCOL_STATE",
                "GET_EXPECTED_REAGENTS",
                "GET_ACTIVE_TIMERS",
                "READ_LAST_ENTRY",
                "ANSWER_PROTOCOL_QUESTION",
            }
        ):
            # The same structured answers a button or the typed question panel
            # gets; a model routed the utterance at most, it never answers.
            message, data = self._read_only_result(uow, session, read_tool, parsed.parameters)
            return data.get("spoken", message)

        version = uow.protocols.get_version(session.protocol_version_id)
        if version is None:
            raise ApplicationError("PROTOCOL_VERSION_NOT_FOUND", "protocol version was not found")
        active_index = next(
            (
                index
                for index, step in enumerate(version.steps)
                if step.id == session.active_step_id
            ),
            None,
        )
        current = version.steps[active_index] if active_index is not None else None

        if parsed.intent in {"GET_CURRENT_STEP", "REPEAT_INSTRUCTION"}:
            if current is None:
                return "There is no active reviewed protocol step."
            return f"Current reviewed step {current.number}: {current.instruction}"
        if parsed.intent == "GET_NEXT_STEP":
            next_index = active_index + 1 if active_index is not None else 0
            if next_index >= len(version.steps):
                return "There is no later reviewed protocol step."
            following = version.steps[next_index]
            return f"Next reviewed step {following.number}: {following.instruction}"
        if parsed.intent == "VERIFY_REAGENT":
            if current is None or not current.requirements:
                return "The current reviewed step has no reagent requirement to compare."
            requested = parsed.parameters.get("requirement_id", "")
            expected = next(
                (item for item in current.requirements if item.requirement_id == requested),
                current.requirements[0],
            )
            quantity = (
                f"{expected.quantity} {expected.unit} "
                if expected.quantity is not None and expected.unit is not None
                else ""
            )
            return (
                f"Show the visible label for the expected {quantity}{expected.name} to the camera. "
                "Label matching does not verify bottle contents or safety."
            )
        if parsed.intent == "CORRECT_PREVIOUS_ENTRY":
            return (
                "Say the new value, for example: correct the last measurement to 12 "
                "millilitres. The original entry will remain."
            )
        if parsed.intent == "HELP":
            return (
                "You can ask for the current or next step, ask a question about the protocol, "
                "record a measurement or observation, set, pause or cancel a timer, ask how "
                "long is left, check a reagent by name, skip a step with a reason, correct the "
                "last measurement, pause, resume, complete a step, and end the session."
            )
        return "No state change was required."

    def verify_reagent(
        self,
        session_id: str,
        image: bytes,
        *,
        content_type: str,
        correlation_id: str,
        expected_version: int,
        idempotency_key: str,
        requirement_id: str | None = None,
        additional_frames: Sequence[bytes] = (),
        capture_metadata: Mapping[str, str] | None = None,
    ) -> ReagentVerification:
        # Preserve the facade's long-standing dependency-injection seam used by
        # failure/concurrency tests and local diagnostics.
        self._reagent_verification.label_adapter = self._labels
        return self._reagent_verification.verify_reagent(
            session_id,
            image,
            content_type=content_type,
            correlation_id=correlation_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requirement_id=requirement_id,
            additional_frames=additional_frames,
            capture_metadata=capture_metadata,
        )

    def probe_reagent_label(
        self,
        session_id: str,
        image: bytes,
        *,
        content_type: str,
        requirement_id: str | None = None,
    ) -> LabelProbe:
        """One live-scan probe (nothing recorded); refused unless live scan is on."""

        if not self._live_label_scan:
            raise ApplicationError(
                "LIVE_LABEL_SCAN_UNAVAILABLE",
                "live label scanning is not enabled for this deployment",
            )
        self._reagent_verification.label_adapter = self._labels
        return self._reagent_verification.probe_label(
            session_id, image, content_type=content_type, requirement_id=requirement_id
        )

    def confirm_reagent_verification(
        self,
        verification_id: str,
        *,
        accepted: bool,
        corrected_observed_label: str | None,
        correlation_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ReagentVerification:
        return self._reagent_verification.confirm_reagent_verification(
            verification_id,
            accepted=accepted,
            corrected_observed_label=corrected_observed_label,
            correlation_id=correlation_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def confirm_reagent_verification_by_voice(
        self,
        verification_id: str,
        audio: bytes,
        *,
        content_type: str,
        correlation_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> SpokenReagentDecisionResult:
        """Answer a pending label reading through the wake-phrase listener.

        Voice-only mode (opt-in). The clip must begin with the wake phrase and
        both transcriptions must agree; "Jarvis, confirm ethanol" confirms a
        MATCH only when the expected reagent is named, and "Jarvis, reject"
        rejects any reading. A MISMATCH or UNCERTAIN reading is never confirmed
        by voice, whatever is said.
        """

        if not self._voice_only_confirmation:
            raise ApplicationError(
                "VOICE_ONLY_CONFIRMATION_DISABLED",
                "voice-only confirmation is switched off; use the screen",
            )
        if self._verification_speech is None:
            raise ApplicationError(
                "HIGH_INTEGRITY_TRANSCRIPTION_UNAVAILABLE",
                "voice-only confirmation needs the high-integrity transcription adapter",
            )
        if not audio:
            raise ApplicationError("AUDIO_REQUIRED", "a confirmation recording is required")

        with self._uow_factory() as uow:
            verification = uow.verifications.get(verification_id)
            if verification is None:
                raise ApplicationError(
                    "REAGENT_VERIFICATION_NOT_FOUND", "reagent verification was not found"
                )
            if verification.decision.value != "PENDING":
                raise ApplicationError(
                    "REAGENT_VERIFICATION_RESOLVED", "reagent verification is already resolved"
                )
            session = self._session(uow, verification.session_id)
            started_at = perf_counter()
            heard = self._run_inference(
                "speech.transcribe",
                lambda: self._speech.transcribe(audio, content_type=content_type),
            )
            if not addressed_to_assistant(heard.transcript, self._wake_phrase):
                return SpokenReagentDecisionResult(
                    decision=VoiceDecision.NOT_ADDRESSED.value,
                    committed=False,
                    verification=verification,
                    response_text="",
                    reasons=("the reply did not start with the wake phrase",),
                )
            control = voice_control(strip_wake_phrase(heard.transcript, self._wake_phrase) or "")
            if control is not None:
                response = CONTROL_RESPONSES[control]
                if control is VoiceControl.ACKNOWLEDGE_ALARM:
                    response = self._acknowledge_alarms(uow, session, correlation_id=correlation_id)
                self._append_audit(
                    uow,
                    session_id=session.id,
                    event_type="voice_control",
                    correlation_id=correlation_id,
                    details={"control": control.value, "verification_id": verification.id},
                )
                return SpokenReagentDecisionResult(
                    decision="control",
                    committed=False,
                    verification=verification,
                    response_text=response,
                    control=control.value,
                )
            precomputed = (heard, max(0, round((perf_counter() - started_at) * 1000)))
            primary, _ = self._transcribe_for_confirmation(
                uow,
                session_id=session.id,
                audio=audio,
                content_type=content_type,
                modality="speech_confirmation",
                precomputed=precomputed,
            )
            second, _ = self._transcribe_for_confirmation(
                uow,
                session_id=session.id,
                audio=audio,
                content_type=content_type,
                modality="speech_confirmation_verification",
                verifier=True,
            )
            comparison = compare_transcripts(primary.transcript, second.transcript)
            canonical = comparison.canonical or ""
            aliases = self._requirement_aliases(uow, session, verification)
            expected = (
                reagent_readback(verification.expected_label, aliases)
                if verification.outcome.value == "MATCH"
                else None
            )
            if canonical:
                verdict = judge_voice_only_confirmation(
                    canonical, wake_phrase=self._wake_phrase, expected=expected
                )
                decision, reasons = verdict.decision, verdict.reasons
            else:
                decision = VoiceDecision.AMBIGUOUS
                reasons = ("the two readings of your reply did not match",)
            if decision is VoiceDecision.NOT_SUPPORTED:
                reasons = (
                    f"a {verification.outcome.value} reading can never be confirmed as "
                    "verified; say the wake word then reject, or scan again",
                )
            self._append_audit(
                uow,
                session_id=session.id,
                event_type="spoken_reagent_decision",
                correlation_id=correlation_id,
                details={
                    "verification_id": verification.id,
                    "outcome": verification.outcome.value,
                    "decision": decision.value,
                },
            )

        if decision in {VoiceDecision.CONFIRM, VoiceDecision.REJECT}:
            accepted = decision is VoiceDecision.CONFIRM
            resolved = self._reagent_verification.confirm_reagent_verification(
                verification_id,
                accepted=accepted,
                corrected_observed_label=None,
                correlation_id=correlation_id,
                expected_version=expected_version,
                idempotency_key=f"voice:{idempotency_key}",
                source=EntrySource.USER_CONFIRMED_SPEECH,
            )
            committed = resolved.decision.value in {"CONFIRMED", "REJECTED"}
            text = (
                (f"Confirmed: the visible label matches {verification.expected_label}."
                 if accepted else "Rejected. The reading was not accepted.")
                if committed
                else "The reading is out of date; nothing was recorded. Scan again."
            )  # fmt: skip
            return SpokenReagentDecisionResult(
                decision=decision.value,
                committed=committed,
                verification=resolved,
                response_text=text,
            )
        prompt = (
            f"Say the wake word, then: {expected.phrase()}, or reject"
            if expected is not None
            else "Say the wake word, then: reject; or scan the label again"
        )
        return SpokenReagentDecisionResult(
            decision=decision.value,
            committed=False,
            verification=verification,
            response_text=(
                f"I could not act on that ({'; '.join(reasons) or 'the reply was not clear'}). "
                f"Nothing was recorded. {prompt}."
            ),
            reasons=reasons,
        )

    @staticmethod
    def _requirement_aliases(
        uow: UnitOfWorkPort, session: LabSession, verification: ReagentVerification
    ) -> tuple[str, ...]:
        version = uow.protocols.get_version(session.protocol_version_id)
        if version is None:
            return ()
        for step in version.steps:
            if step.id != verification.step_id:
                continue
            for requirement in step.requirements:
                if requirement.requirement_id == verification.requirement_id:
                    return tuple(requirement.aliases)
        return ()
    def correct_notebook_entry(
        self,
        entry_id: str,
        payload: Mapping[str, str],
        *,
        correlation_id: str,
    ) -> NotebookEntry:
        with self._uow_factory() as uow:
            original = uow.notebook.get(entry_id)
            if original is None:
                raise ApplicationError("NOTEBOOK_ENTRY_NOT_FOUND", "notebook entry was not found")
            corrected = original.corrected(
                entry_id=self._new_id(),
                payload=payload,
                source=EntrySource.TYPED_INPUT,
                correlation_id=correlation_id,
                at=self._clock(),
            )
            uow.notebook.append(corrected)
            self._append_audit(
                uow,
                session_id=original.session_id,
                event_type="notebook_entry_corrected",
                correlation_id=correlation_id,
                details={"entry_id": original.id, "correction_id": corrected.id},
            )
        return corrected

    def export_notebook(self, session_id: str, export_format: str) -> NotebookExport:
        try:
            if export_format.casefold() == "json":
                return self._notebook_export.export_json(session_id)
            if export_format.casefold() == "csv":
                return self._notebook_export.export_csv(session_id)
            raise ApplicationError(
                "EXPORT_FORMAT_UNSUPPORTED",
                "export format must be json or csv",
            )
        except NotebookExportError as exc:
            raise ApplicationError(exc.code, str(exc)) from exc

    def _speech_vocabulary(self, uow: UnitOfWorkPort, session: LabSession) -> tuple[str, ...]:
        """Protocol-derived recognition context from the session's approved version."""

        return protocol_vocabulary(uow.protocols.get_version(session.protocol_version_id))

    def _command_context(self, uow: UnitOfWorkPort, session: LabSession) -> CommandContext:
        instruction: str | None = None
        version = uow.protocols.get_version(session.protocol_version_id)
        if version is not None and session.active_step_id is not None:
            step = next(
                (
                    candidate
                    for candidate in version.steps
                    if candidate.id == session.active_step_id
                ),
                None,
            )
            instruction = step.instruction if step is not None else None
        return CommandContext(
            session_id=session.id,
            session_state=session.state.value,
            active_step_id=session.active_step_id,
            active_instruction=instruction,
        )

    @staticmethod
    def _session(uow: UnitOfWorkPort, session_id: str) -> LabSession:
        session = uow.sessions.get(session_id)
        if session is None:
            raise ApplicationError("SESSION_NOT_FOUND", "session was not found")
        return session

    @staticmethod
    def _require_version(session: LabSession, expected_version: int) -> None:
        if session.version != expected_version:
            raise ApplicationError("SESSION_VERSION_CONFLICT", "session version changed")

    @staticmethod
    def _request_hash(*parts: str) -> str:
        return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def _run_inference(
        self,
        operation: str,
        call: Callable[[], Any],
    ) -> Any:
        """Mark a model exception so the outer transaction owner can persist it."""

        started_at = perf_counter()
        try:
            return self._inference.run(operation, call)
        except Exception as exc:
            if isinstance(exc, ModelAdapterError):
                exc.labjarvis_operation = operation
                exc.labjarvis_latency_ms = max(
                    0,
                    round((perf_counter() - started_at) * 1000),
                )
            raise

    def _record_marked_inference_failure(
        self,
        error: Exception,
        *,
        session_id: str,
        input_sha256: str,
        correlation_id: str,
        modality_override: str | None = None,
    ) -> None:
        operation = getattr(error, "labjarvis_operation", None)
        if not isinstance(operation, str):
            return
        adapter: object
        if operation == "text.interpret":
            modality = "text"
            adapter = self._text
        elif operation == "vision.recognise":
            modality = "vision"
            adapter = self._labels
        elif operation == "speech.verify":
            modality = "speech_verification"
            adapter = self._verification_speech
        else:
            modality = "speech"
            adapter = self._speech
        if modality_override is not None:
            modality = modality_override
        latency_ms = getattr(error, "labjarvis_latency_ms", 0)
        self._inference_provenance.record_failure(
            FailedInferenceContext(
                session_id=session_id,
                modality=modality,
                operation=operation,
                adapter=str(getattr(adapter, "adapter_name", "unknown-adapter")),
                model_name=str(getattr(adapter, "model_name", "unknown-model")),
                latency_ms=latency_ms if isinstance(latency_ms, int) else 0,
                input_sha256=input_sha256,
                correlation_id=correlation_id,
                error=error,
            )
        )

    def _pending_action_session_id(self, action_id: str) -> str:
        with self._uow_factory() as uow:
            action = uow.pending_actions.get(action_id)
            if action is None:
                raise ApplicationError(
                    "PENDING_ACTION_NOT_FOUND",
                    "pending action was not found",
                )
            return action.session_id

    def _append_audit(
        self,
        uow: UnitOfWorkPort,
        *,
        session_id: str | None,
        event_type: str,
        correlation_id: str,
        details: Mapping[str, str],
    ) -> None:
        uow.audit.append(
            AuditEvent(
                id=self._new_id(),
                session_id=session_id,
                event_type=event_type,
                actor="local_operator",
                correlation_id=correlation_id,
                details=details,
                created_at=self._clock(),
            )
        )

    def _retain_raw_media(
        self,
        *,
        session_id: str,
        modality: str,
        content_type: str,
        data: bytes,
    ) -> StoredRawMedia | None:
        try:
            return self._raw_media.store(
                session_id=session_id,
                modality=modality,
                content_type=content_type,
                data=data,
            )
        except RawMediaStorageError as exc:
            raise ApplicationError(
                "RAW_MEDIA_STORAGE_FAILED",
                "raw media could not be retained safely",
            ) from exc

    @staticmethod
    def _retention_metadata(retained: StoredRawMedia | None) -> dict[str, str]:
        if retained is None:
            return {"raw_media_retained": "false"}
        return {
            "raw_media_retained": "true",
            "raw_media_reference": retained.reference,
            "raw_media_content_type": retained.content_type,
            "raw_media_byte_count": str(retained.byte_count),
        }

    def _transcript_metadata(self, transcript: str) -> dict[str, str]:
        if self._retain_transcripts:
            return {"transcript": transcript, "transcript_retained": "true"}
        return {
            "transcript_sha256": sha256(transcript.encode("utf-8")).hexdigest(),
            "transcript_retained": "false",
        }

    def _speech_inference_output(
        self,
        transcript: str,
        retained: StoredRawMedia | None,
        *,
        metadata: Mapping[str, str],
    ) -> dict[str, str]:
        return {
            **self._transcript_metadata(transcript),
            **dict(metadata),
            **self._retention_metadata(retained),
        }


_TIMER_VERBS = {"PAUSE_TIMER": "pause", "RESUME_TIMER": "resume", "CANCEL_TIMER": "cancel"}
_TIMER_GERUNDS = {
    "PAUSE_TIMER": "pausing",
    "RESUME_TIMER": "resuming",
    "CANCEL_TIMER": "cancelling",
}


def _match_timers(
    views: Sequence[TimerView], reference: str, version: ProtocolVersion | None
) -> list[TimerView]:
    step_numbers = {step.id: step.number for step in version.steps} if version is not None else {}
    timers = [view.timer for view in views]
    chosen = {timer.id for timer in match_timers(timers, reference, step_numbers)}
    return [view for view in views if view.timer.id in chosen]


def _match_requirement(
    requirements: Sequence[ProtocolRequirement], reference: str
) -> ProtocolRequirement | None:
    return match_requirement(requirements, reference)
