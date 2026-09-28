"""The machine-readable export contains the complete session aggregate."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO

from labjarvis.application.notebook_export import NotebookExportService
from labjarvis.domain.models import (
    AuditEvent,
    EntrySource,
    ExtractionFlag,
    LabSession,
    ModelInference,
    NotebookEntry,
    PendingAction,
    PendingActionStatus,
    Protocol,
    ProtocolStepDraft,
    ProtocolVersion,
    ProtocolVersionStatus,
    ReagentVerification,
    SessionState,
    SessionStep,
    SessionTimer,
    SourceReference,
    StepState,
    TimerState,
    UnassignedSourceText,
    Utterance,
    VerificationDecision,
    VerificationOutcome,
)
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory

NOW = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def populated_export_service(tmp_path) -> tuple[NotebookExportService, str]:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'notebook-export.db'}",
        create_schema=True,
    )
    step = ProtocolStepDraft(
        id="step-1",
        number=1,
        title="Observe",
        instruction="Observe the sample.",
        requirements=(),
        duration_seconds=60,
        safety_notes=("Wear eye protection.",),
        source=SourceReference(page=1, section="Method", excerpt="Observe the sample."),
        flags=(
            ExtractionFlag(
                code="TIMING_APPROXIMATE",
                message="The source gives an approximate time, so no timer was set.",
                excerpt="about 1 minute",
                acknowledged=True,
            ),
        ),
    )
    protocol = Protocol(id="protocol-1", title="Export protocol", created_at=NOW)
    version = ProtocolVersion(
        id="version-1",
        protocol_id=protocol.id,
        version_number=1,
        revision=2,
        status=ProtocolVersionStatus.APPROVED,
        steps=(step,),
        source_sha256="a" * 64,
        source_text="Observe the sample.",
        created_at=NOW,
        approved_at=NOW,
        unassigned_text=(
            UnassignedSourceText(
                page=1, section="Materials", text="- Sample tube", reason="NON_PROCEDURE_SECTION"
            ),
        ),
    )
    session = LabSession(
        id="session-1",
        protocol_version_id=version.id,
        protocol_approved=True,
        state=SessionState.COMPLETED,
        steps=(
            SessionStep(
                step_id=step.id,
                state=StepState.COMPLETED,
                started_at=NOW,
                completed_at=NOW + timedelta(minutes=1),
            ),
        ),
        active_step_id=None,
        version=2,
        created_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=1),
    )
    inference = ModelInference(
        id="inference-1",
        session_id=session.id,
        modality="text",
        adapter="test-adapter",
        model_name="test-model",
        status="failed",
        confidence=None,
        latency_ms=5,
        input_sha256="b" * 64,
        output={"error_code": "MODEL_TIMEOUT"},
        created_at=NOW,
    )
    original = NotebookEntry(
        id="entry-1",
        session_id=session.id,
        entry_type="observation",
        source=EntrySource.TYPED_INPUT,
        payload={"observation": "clear"},
        created_at=NOW,
        correlation_id="correlation-1",
        model_inference_id=inference.id,
    )
    correction = original.corrected(
        entry_id="entry-2",
        payload={"observation": "cloudy"},
        source=EntrySource.TYPED_INPUT,
        correlation_id="correlation-2",
        at=NOW + timedelta(seconds=1),
    )
    verification = ReagentVerification(
        id="verification-1",
        session_id=session.id,
        step_id=step.id,
        requirement_id="step-1-req-1",
        expected_label="ethanol",
        observed_label="ethanol",
        outcome=VerificationOutcome.MATCH,
        score=100,
        expected_normalised="ethanol",
        observed_normalised="ethanol",
        match_threshold=92,
        uncertain_threshold=72,
        model_confidence=1,
        decision=VerificationDecision.STALE,
        model_inference_id=inference.id,
        created_at=NOW,
        resolved_at=NOW + timedelta(seconds=2),
    )
    timer = SessionTimer(
        id="timer-1",
        session_id=session.id,
        step_id=step.id,
        label="incubation",
        duration_seconds=60,
        state=TimerState.CANCELLED,
        created_at=NOW,
        started_at=NOW,
        deadline_at=None,
        remaining_seconds_at_pause=None,
        paused_at=None,
        resolved_at=NOW + timedelta(seconds=3),
        correlation_id="correlation-3",
        version=1,
    )
    action = PendingAction(
        id="action-1",
        session_id=session.id,
        intent="COMPLETE_STEP",
        payload={"step_id": step.id},
        source=EntrySource.MODEL_PROPOSAL,
        status=PendingActionStatus.PENDING,
        expected_session_version=1,
        created_at=NOW,
        correlation_id="correlation-4",
        model_inference_id=inference.id,
    )
    utterance = Utterance(
        id="utterance-1",
        session_id=session.id,
        realtime_transcript="complete step",
        verification_transcript="complete step",
        canonical_transcript="complete step",
        agreement="AGREE",
        reasons=(),
        primary_model="test-model",
        verification_model="test-model",
        primary_inference_id=inference.id,
        verification_inference_id=inference.id,
        confirmed_transcript="complete step",
        created_at=NOW,
        correlation_id="correlation-5",
        pending_action_id=action.id,
        audio_sha256="c" * 64,
        decision="confirm",
    )
    expired_action = replace(
        action,
        id="action-2",
        created_at=NOW + timedelta(seconds=1),
        correlation_id="correlation-6",
    )
    open_action = replace(
        action,
        id="action-3",
        created_at=NOW + timedelta(seconds=2),
        correlation_id="correlation-7",
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.protocols.add(protocol, version)
        uow.sessions.add(session)
        uow.inferences.append(inference)
        uow.notebook.append(original)
        uow.notebook.append(correction)
        uow.verifications.append(verification)
        uow.timers.add(timer)
        uow.pending_actions.add(action)
        uow.pending_actions.resolve(
            replace(action, status=PendingActionStatus.ACCEPTED, resolved_at=NOW),
            expected_status=PendingActionStatus.PENDING,
        )
        uow.pending_actions.add(expired_action)
        uow.pending_actions.expire(
            replace(
                expired_action,
                status=PendingActionStatus.EXPIRED,
                resolved_at=NOW + timedelta(seconds=2),
            )
        )
        uow.pending_actions.add(open_action)
        uow.utterances.append(utterance)
        uow.audit.append(
            AuditEvent(
                id="audit-1",
                session_id=session.id,
                event_type="step_completed",
                actor="operator",
                correlation_id="correlation-4",
                details={"action_id": action.id},
                created_at=NOW,
            )
        )
    return (
        NotebookExportService(
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            clock=lambda: NOW + timedelta(minutes=2),
            application_commit="abc123",
        ),
        session.id,
    )


def test_json_export_contains_complete_session_provenance(tmp_path) -> None:
    service, session_id = populated_export_service(tmp_path)

    payload = json.loads(service.export_json(session_id).content)

    assert set(payload) == {
        "schema_name",
        "schema_version",
        "exported_at",
        "application_commit",
        "session",
        "protocol",
        "notebook_entries",
        "inferences",
        "reagent_verifications",
        "timers",
        "audit_events",
        "utterances",
        "pending_actions",
    }
    assert payload["schema_name"] == "labjarvis.session"
    assert payload["schema_version"] == "2.0"
    assert payload["application_commit"] == "abc123"
    assert payload["protocol"]["version"]["steps"][0]["source"]["page"] == 1
    # The import review travels with the export: what was flagged, whether the
    # reviewer acknowledged it, and the source text no step used.
    assert payload["protocol"]["version"]["steps"][0]["flags"] == [
        {
            "code": "TIMING_APPROXIMATE",
            "message": "The source gives an approximate time, so no timer was set.",
            "excerpt": "about 1 minute",
            "acknowledged": True,
        }
    ]
    assert payload["protocol"]["version"]["unassigned_text"] == [
        {
            "page": 1,
            "section": "Materials",
            "text": "- Sample tube",
            "reason": "NON_PROCEDURE_SECTION",
        }
    ]
    assert payload["notebook_entries"][1]["supersedes_entry_id"] == "entry-1"
    assert payload["inferences"][0]["status"] == "failed"
    assert payload["reagent_verifications"][0]["decision"] == "STALE"
    assert payload["timers"][0]["state"] == "CANCELLED"
    assert [item["status"] for item in payload["pending_actions"]] == [
        "ACCEPTED",
        "EXPIRED",
        "PENDING",
    ]
    assert payload["utterances"][0]["primary_inference_id"] == payload["inferences"][0]["id"]
    assert payload["utterances"][0]["pending_action_id"] == payload["pending_actions"][0]["id"]


def test_csv_export_is_labelled_as_a_flattened_notebook_subset(tmp_path) -> None:
    service, session_id = populated_export_service(tmp_path)

    exported = service.export_csv(session_id)
    rows = list(csv.DictReader(StringIO(exported.content.decode("utf-8"))))

    assert exported.media_type == "text/csv; charset=utf-8"
    assert rows[0]["export_schema_name"] == "labjarvis.session"
    assert rows[0]["export_schema_version"] == "2.0-csv-notebook-subset"
    assert rows[0]["session_id"] == session_id
    assert rows[1]["supersedes_entry_id"] == "entry-1"
