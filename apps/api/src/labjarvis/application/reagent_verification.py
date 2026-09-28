"""Versioned, idempotent visible-label verification workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from time import perf_counter
from typing import Any

from labjarvis.application.errors import ApplicationError
from labjarvis.application.inference_provenance import (
    FailedInferenceContext,
    InferenceProvenanceService,
)
from labjarvis.domain.models import (
    AuditEvent,
    EntrySource,
    IdempotencyRecord,
    LabSession,
    ModelInference,
    NotebookEntry,
    ProtocolRequirement,
    ProtocolStepDraft,
    ReagentVerification,
    SessionState,
    VerificationDecision,
)
from labjarvis.domain.reagent import classify_reagent
from labjarvis.domain.reagent_consensus import (
    MAX_FRAMES,
    FrameReading,
    unanimous_consensus,
)
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.ports.media import RawMediaStorageError, RawMediaStorePort, StoredRawMedia
from labjarvis.ports.models import InferenceRunnerPort, LabelRecognitionPort
from labjarvis.ports.repositories import RepositoryConflict, UnitOfWorkPort

UowFactory = Callable[[], UnitOfWorkPort]


@dataclass(frozen=True, slots=True)
class ReagentAuthority:
    """Immutable authority captured before label recognition starts."""

    session_version: int
    step_id: str
    requirement: ProtocolRequirement


@dataclass(frozen=True, slots=True)
class LabelProbe:
    """One live-scan reading. Never persisted; see ``probe_label``."""

    outcome: str
    observed_normalised: str
    visible_text: str
    score: float
    requirement_id: str
    expected_label: str


class ReagentVerificationService:
    """Own the three-stage OCR/revalidation/commit workflow."""

    def __init__(
        self,
        *,
        uow_factory: UowFactory,
        labels: LabelRecognitionPort,
        inference_runner: InferenceRunnerPort,
        inference_provenance: InferenceProvenanceService,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
        match_threshold: float,
        uncertain_threshold: float,
        raw_media_store: RawMediaStorePort,
    ) -> None:
        self._uow_factory = uow_factory
        self._labels = labels
        self._inference = inference_runner
        self._inference_provenance = inference_provenance
        self._clock = clock
        self._new_id = new_id
        self._match_threshold = match_threshold
        self._uncertain_threshold = uncertain_threshold
        self._raw_media = raw_media_store

    @property
    def label_adapter(self) -> LabelRecognitionPort:
        """Current adapter, exposed for controlled dependency replacement in tests."""

        return self._labels

    @label_adapter.setter
    def label_adapter(self, adapter: LabelRecognitionPort) -> None:
        self._labels = adapter

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
        """Recognise outside a transaction, then revalidate authority before commit.

        ``capture_metadata`` describes how the frames were chosen (for example a
        live scan's stability rule and how many probes it took); it is recorded
        in the proposal for provenance and changes nothing about the decision.

        With ``additional_frames`` every frame is read and compared separately
        and the outcome is the deterministic unanimous consensus
        (``domain.reagent_consensus``): any disagreement or unreadable frame
        becomes ``UNCERTAIN``. A single frame behaves exactly as before.
        """

        if not idempotency_key.strip():
            raise ApplicationError("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
        frames = (image, *additional_frames)
        if len(frames) > MAX_FRAMES:
            raise ApplicationError(
                "REAGENT_TOO_MANY_FRAMES", f"at most {MAX_FRAMES} frames are accepted"
            )
        if any(not frame for frame in frames):
            raise ApplicationError("REAGENT_FRAME_EMPTY", "every frame must contain image data")
        frame_hashes = [sha256(frame).hexdigest() for frame in frames]
        image_sha256 = frame_hashes[0] if len(frames) == 1 else sha256(
            "".join(frame_hashes).encode("ascii")
        ).hexdigest()
        scope = f"reagent-upload:{session_id}"
        request_hash = self._request_hash(
            session_id,
            str(expected_version),
            requirement_id or "",
            image_sha256,
            content_type,
        )

        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                return self._replay(uow, replay, request_hash)
            session = self._session(uow, session_id)
            self._require_version(session, expected_version)
            self._require_no_pending_action(uow, session.id)
            if session.state is not SessionState.ACTIVE or session.active_step_id is None:
                raise ApplicationError("REAGENT_VERIFICATION_UNAVAILABLE", "no active step")
            version = uow.protocols.get_version(session.protocol_version_id)
            if version is None:
                raise ApplicationError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            step = next(
                (item for item in version.steps if item.id == session.active_step_id),
                None,
            )
            if step is None or not step.requirements:
                raise ApplicationError(
                    "REAGENT_REQUIREMENT_NOT_FOUND",
                    "active step has no reagent requirement",
                )
            expected = self._select_requirement(step, requirement_id)
            if (
                uow.verifications.get_pending(
                    session.id,
                    step.id,
                    expected.requirement_id,
                )
                is not None
            ):
                raise ApplicationError(
                    "REAGENT_VERIFICATION_PENDING",
                    "resolve the current reagent review before uploading another image",
                )
            authority = ReagentAuthority(
                session_version=session.version,
                step_id=step.id,
                requirement=expected,
            )

        readings: list[tuple[Any, int, StoredRawMedia | None, str]] = []
        for frame, frame_hash in zip(frames, frame_hashes, strict=True):
            started_at = perf_counter()
            try:
                label = self._run_inference(
                    "vision.recognise",
                    lambda frame=frame: self._labels.recognise(  # type: ignore[misc]
                        frame, content_type=content_type
                    ),
                )
            except Exception as exc:
                self._record_inference_failure(
                    exc,
                    session_id=session_id,
                    input_sha256=frame_hash,
                    correlation_id=correlation_id,
                )
                raise
            latency_ms = max(0, round((perf_counter() - started_at) * 1000))
            retained_frame = self._retain_raw_media(
                session_id=session_id,
                modality="image",
                content_type=content_type,
                data=frame,
            )
            readings.append((label, latency_ms, retained_frame, frame_hash))
        frame_readings = [
            FrameReading(
                visible_text=reading[0].visible_text,
                comparison=classify_reagent(
                    reading[0].visible_text,
                    authority.requirement.name,
                    aliases=authority.requirement.aliases,
                    match_threshold=self._match_threshold,
                    uncertain_threshold=self._uncertain_threshold,
                ),
            )
            for reading in readings
        ]
        consensus = unanimous_consensus(frame_readings)
        label = readings[0][0]
        comparison = replace(
            frame_readings[0].comparison,
            outcome=consensus.outcome,
            score=consensus.score,
            observed_normalised=consensus.observed_normalised,
        )
        observed_text = consensus.observed_text

        try:
            with self._uow_factory() as uow:
                replay = uow.idempotency.get(scope, idempotency_key)
                if replay is not None:
                    return self._replay(uow, replay, request_hash)
                session = self._session(uow, session_id)
                self._require_version(session, authority.session_version)
                self._require_no_pending_action(uow, session.id)
                if (
                    session.state is not SessionState.ACTIVE
                    or session.active_step_id != authority.step_id
                ):
                    raise ApplicationError(
                        "REAGENT_VERIFICATION_STALE",
                        "the active step changed while the label was being read",
                    )
                version = uow.protocols.get_version(session.protocol_version_id)
                step = (
                    next(
                        (item for item in version.steps if item.id == authority.step_id),
                        None,
                    )
                    if version is not None
                    else None
                )
                if step is None:
                    raise ApplicationError(
                        "REAGENT_REQUIREMENT_NOT_FOUND",
                        "the reagent requirement is no longer available",
                    )
                expected = self._select_requirement(
                    step,
                    authority.requirement.requirement_id,
                )
                if (
                    uow.verifications.get_pending(
                        session.id,
                        step.id,
                        expected.requirement_id,
                    )
                    is not None
                ):
                    raise ApplicationError(
                        "REAGENT_VERIFICATION_PENDING",
                        "resolve the current reagent review before uploading another image",
                    )
                inference_id = ""
                for index, (reading, frame_reading) in enumerate(
                    zip(readings, frame_readings, strict=True)
                ):
                    frame_label, frame_latency, frame_retained, frame_hash = reading
                    frame_inference_id = self._new_id()
                    inference_id = inference_id or frame_inference_id
                    uow.inferences.append(
                        ModelInference(
                            id=frame_inference_id,
                            session_id=session_id,
                            modality="vision",
                            adapter=frame_label.adapter,
                            model_name=frame_label.model_name,
                            status="succeeded",
                            confidence=frame_label.confidence,
                            latency_ms=frame_latency,
                            input_sha256=frame_hash,
                            output={
                                "visible_text": frame_label.visible_text,
                                "frame_index": str(index),
                                "frame_count": str(len(readings)),
                                "frame_outcome": frame_reading.comparison.outcome.value,
                                **dict(frame_label.metadata),
                                **self._retention_metadata(frame_retained),
                            },
                            created_at=self._clock(),
                        )
                    )
                verification = ReagentVerification(
                    id=self._new_id(),
                    session_id=session_id,
                    step_id=step.id,
                    requirement_id=expected.requirement_id,
                    expected_label=expected.name,
                    observed_label=observed_text,
                    outcome=comparison.outcome,
                    score=comparison.score,
                    expected_normalised=comparison.expected_normalised,
                    observed_normalised=comparison.observed_normalised,
                    match_threshold=self._match_threshold,
                    uncertain_threshold=self._uncertain_threshold,
                    model_confidence=label.confidence,
                    decision=VerificationDecision.PENDING,
                    model_inference_id=inference_id,
                    created_at=self._clock(),
                )
                uow.verifications.append(verification)
                uow.notebook.append(
                    NotebookEntry(
                        id=self._new_id(),
                        session_id=session_id,
                        entry_type="reagent_verification_proposal",
                        source=EntrySource.MODEL_PROPOSAL,
                        payload={
                            "requirement_id": expected.requirement_id,
                            "expected": expected.name,
                            "observed": observed_text,
                            "outcome": comparison.outcome.value,
                            "frame_count": str(len(readings)),
                            "consensus_algorithm": consensus.algorithm,
                            "consensus_reason": consensus.reason,
                            "frame_outcomes": ",".join(
                                outcome.value for outcome in consensus.frame_outcomes
                            ),
                            "score": str(comparison.score),
                            "expected_normalised": comparison.expected_normalised,
                            "observed_normalised": comparison.observed_normalised,
                            "match_threshold": str(self._match_threshold),
                            "uncertain_threshold": str(self._uncertain_threshold),
                            "decision": VerificationDecision.PENDING.value,
                            **{
                                f"capture_{key}": str(value)[:120]
                                for key, value in (capture_metadata or {}).items()
                            },
                        },
                        created_at=self._clock(),
                        correlation_id=correlation_id,
                        model_inference_id=inference_id,
                    )
                )
                self._append_audit(
                    uow,
                    session_id=session_id,
                    event_type="reagent_verification_proposed",
                    correlation_id=correlation_id,
                    details={
                        "outcome": comparison.outcome.value,
                        "step_id": step.id,
                        "requirement_id": expected.requirement_id,
                    },
                )
                uow.idempotency.add(
                    IdempotencyRecord(
                        scope=scope,
                        key=idempotency_key,
                        request_sha256=request_hash,
                        response={"verification_id": verification.id},
                        created_at=self._clock(),
                    )
                )
            return verification
        except RepositoryConflict as exc:
            with self._uow_factory() as uow:
                replay = uow.idempotency.get(scope, idempotency_key)
                if replay is not None:
                    return self._replay(uow, replay, request_hash)
            raise ApplicationError(exc.code, str(exc)) from exc

    def confirm_reagent_verification(
        self,
        verification_id: str,
        *,
        accepted: bool,
        corrected_observed_label: str | None,
        correlation_id: str,
        expected_version: int,
        idempotency_key: str,
        source: EntrySource = EntrySource.TYPED_INPUT,
    ) -> ReagentVerification:
        if not idempotency_key.strip():
            raise ApplicationError("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
        normalized_correction = (
            corrected_observed_label.strip()
            if corrected_observed_label is not None
            else None
        )
        request_hash = self._request_hash(
            verification_id,
            str(expected_version),
            str(accepted).lower(),
            normalized_correction or "",
        )
        scope = f"reagent-decision:{verification_id}"
        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                return self._replay(uow, replay, request_hash)
            verification = uow.verifications.get(verification_id)
            if verification is None:
                raise ApplicationError(
                    "REAGENT_VERIFICATION_NOT_FOUND",
                    "reagent verification was not found",
                )
            if verification.decision is not VerificationDecision.PENDING:
                raise ApplicationError(
                    "REAGENT_VERIFICATION_RESOLVED",
                    "reagent verification is already resolved",
                )
            if corrected_observed_label is not None and not accepted:
                raise ApplicationError(
                    "REAGENT_VERIFICATION_CORRECTION_INVALID",
                    "a corrected label can only be accepted",
                )

            observed = normalized_correction or verification.observed_label
            if not observed or len(observed) > 250:
                raise ApplicationError(
                    "REAGENT_LABEL_INVALID",
                    "corrected visible label must contain 1 to 250 characters",
                )
            session = self._session(uow, verification.session_id)
            version = uow.protocols.get_version(session.protocol_version_id)
            step = (
                next(
                    (
                        candidate
                        for candidate in version.steps
                        if candidate.id == verification.step_id
                    ),
                    None,
                )
                if version is not None
                else None
            )
            requirement = (
                next(
                    (
                        candidate
                        for candidate in step.requirements
                        if candidate.requirement_id == verification.requirement_id
                    ),
                    None,
                )
                if step is not None
                else None
            )
            stale_reason: str | None = None
            if session.version != expected_version:
                stale_reason = "session_version_changed"
            elif session.state is not SessionState.ACTIVE:
                stale_reason = "session_not_active"
            elif session.active_step_id != verification.step_id:
                stale_reason = "active_step_changed"
            elif requirement is None:
                stale_reason = "requirement_unavailable"

            if stale_reason is not None:
                stale = replace(
                    verification,
                    decision=VerificationDecision.STALE,
                    resolved_at=self._clock(),
                )
                uow.verifications.resolve(
                    stale,
                    expected_decision=VerificationDecision.PENDING,
                )
                self._append_audit(
                    uow,
                    session_id=verification.session_id,
                    event_type="reagent_verification_stale",
                    correlation_id=correlation_id,
                    details={
                        "verification_id": verification.id,
                        "reason": stale_reason,
                    },
                )
                uow.idempotency.add(
                    IdempotencyRecord(
                        scope=scope,
                        key=idempotency_key,
                        request_sha256=request_hash,
                        response={"verification_id": stale.id},
                        created_at=self._clock(),
                    )
                )
                return stale

            aliases = requirement.aliases if requirement is not None else ()
            comparison = classify_reagent(
                observed,
                verification.expected_label,
                aliases=aliases,
                match_threshold=verification.match_threshold,
                uncertain_threshold=verification.uncertain_threshold,
            )
            decision = (
                VerificationDecision.CORRECTED
                if corrected_observed_label is not None
                else (
                    VerificationDecision.CONFIRMED
                    if accepted
                    else VerificationDecision.REJECTED
                )
            )
            resolved = replace(
                verification,
                observed_label=observed,
                observed_normalised=comparison.observed_normalised,
                outcome=comparison.outcome,
                score=comparison.score,
                decision=decision,
                resolved_at=self._clock(),
            )
            uow.verifications.resolve(
                resolved,
                expected_decision=VerificationDecision.PENDING,
            )
            uow.notebook.append(
                NotebookEntry(
                    id=self._new_id(),
                    session_id=verification.session_id,
                    entry_type="reagent_verification_confirmation",
                    source=source,
                    payload={
                        "verification_id": verification.id,
                        "accepted": str(accepted).lower(),
                        "decision": decision.value,
                        "expected": verification.expected_label,
                        "observed": observed,
                        "outcome": comparison.outcome.value,
                        "score": str(comparison.score),
                    },
                    created_at=self._clock(),
                    correlation_id=correlation_id,
                    model_inference_id=verification.model_inference_id,
                )
            )
            self._append_audit(
                uow,
                session_id=verification.session_id,
                event_type="reagent_verification_resolved",
                correlation_id=correlation_id,
                details={"verification_id": verification.id, "decision": decision.value},
            )
            uow.idempotency.add(
                IdempotencyRecord(
                    scope=scope,
                    key=idempotency_key,
                    request_sha256=request_hash,
                    response={"verification_id": resolved.id},
                    created_at=self._clock(),
                )
            )
        return resolved

    def probe_label(
        self,
        session_id: str,
        image: bytes,
        *,
        content_type: str,
        requirement_id: str | None = None,
    ) -> LabelProbe:
        """Read one live-scan frame and compare it, recording nothing at all.

        A probe is not evidence: no inference row, no retained image, no
        notebook entry, no audit event. Only the frames a stable scan selects
        are later submitted to :meth:`verify_reagent`, which records them.
        """

        if not image:
            raise ApplicationError("REAGENT_FRAME_EMPTY", "the frame must contain image data")
        with self._uow_factory() as uow:
            session = self._session(uow, session_id)
            if session.state is not SessionState.ACTIVE or session.active_step_id is None:
                raise ApplicationError("REAGENT_VERIFICATION_UNAVAILABLE", "no active step")
            version = uow.protocols.get_version(session.protocol_version_id)
            step = (
                next((item for item in version.steps if item.id == session.active_step_id), None)
                if version is not None
                else None
            )
            if step is None or not step.requirements:
                raise ApplicationError(
                    "REAGENT_REQUIREMENT_NOT_FOUND", "active step has no reagent requirement"
                )
            expected = self._select_requirement(step, requirement_id)
        label = self._run_inference(
            "vision.recognise",
            lambda: self._labels.recognise(image, content_type=content_type),
        )
        comparison = classify_reagent(
            label.visible_text,
            expected.name,
            aliases=expected.aliases,
            match_threshold=self._match_threshold,
            uncertain_threshold=self._uncertain_threshold,
        )
        readable = bool(comparison.observed_normalised.strip())
        return LabelProbe(
            outcome=comparison.outcome.value if readable else "UNREADABLE",
            observed_normalised=comparison.observed_normalised,
            visible_text=label.visible_text[:250],
            score=comparison.score,
            requirement_id=expected.requirement_id,
            expected_label=expected.name,
        )

    @staticmethod
    def _replay(
        uow: UnitOfWorkPort,
        record: IdempotencyRecord,
        request_hash: str,
    ) -> ReagentVerification:
        if record.request_sha256 != request_hash:
            raise ApplicationError(
                "IDEMPOTENCY_CONFLICT",
                "idempotency key was already used for different input",
            )
        verification_id = record.response.get("verification_id", "")
        verification = uow.verifications.get(verification_id)
        if verification is None:
            raise ApplicationError(
                "IDEMPOTENCY_RECORD_INVALID",
                "recorded reagent response could not be restored",
            )
        return verification

    @staticmethod
    def _select_requirement(
        step: ProtocolStepDraft,
        requirement_id: str | None,
    ) -> ProtocolRequirement:
        if requirement_id is None:
            if len(step.requirements) == 1:
                return step.requirements[0]
            raise ApplicationError(
                "REAGENT_REQUIREMENT_AMBIGUOUS",
                "this step lists several reagents; choose which one to verify",
            )
        selected = next(
            (
                candidate
                for candidate in step.requirements
                if candidate.requirement_id == requirement_id
            ),
            None,
        )
        if selected is None:
            raise ApplicationError(
                "REAGENT_REQUIREMENT_NOT_FOUND",
                "the requested reagent requirement is not part of the active step",
            )
        return selected

    def _run_inference(self, operation: str, call: Callable[[], Any]) -> Any:
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

    def _record_inference_failure(
        self,
        error: Exception,
        *,
        session_id: str,
        input_sha256: str,
        correlation_id: str,
    ) -> None:
        operation = getattr(error, "labjarvis_operation", None)
        if not isinstance(operation, str):
            return
        latency_ms = getattr(error, "labjarvis_latency_ms", 0)
        self._inference_provenance.record_failure(
            FailedInferenceContext(
                session_id=session_id,
                modality="vision",
                operation=operation,
                adapter=str(getattr(self._labels, "adapter_name", "unknown-adapter")),
                model_name=str(getattr(self._labels, "model_name", "unknown-model")),
                latency_ms=latency_ms if isinstance(latency_ms, int) else 0,
                input_sha256=input_sha256,
                correlation_id=correlation_id,
                error=error,
            )
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
    def _require_no_pending_action(uow: UnitOfWorkPort, session_id: str) -> None:
        if uow.pending_actions.get_pending_for_session(session_id) is not None:
            raise ApplicationError(
                "PENDING_ACTION_EXISTS",
                "resolve the current proposal before starting another action",
            )

    @staticmethod
    def _request_hash(*parts: str) -> str:
        return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

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
