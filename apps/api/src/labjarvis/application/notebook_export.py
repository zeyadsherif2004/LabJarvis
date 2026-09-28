"""Versioned, complete session exports and an explicitly flattened CSV view."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Any

from labjarvis.domain.models import ProtocolRequirement, ProtocolStepDraft
from labjarvis.ports.repositories import UnitOfWorkPort

SCHEMA_NAME = "labjarvis.session"
SCHEMA_VERSION = "2.0"
CSV_SCHEMA_VERSION = "2.0-csv-notebook-subset"


@dataclass(frozen=True, slots=True)
class NotebookExport:
    filename: str
    media_type: str
    content: bytes


class NotebookExportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _requirement(requirement: ProtocolRequirement) -> dict[str, Any]:
    return {
        "requirement_id": requirement.requirement_id,
        "name": requirement.name,
        "quantity": str(requirement.quantity) if requirement.quantity is not None else None,
        "unit": requirement.unit,
        "aliases": list(requirement.aliases),
    }


def _step(step: ProtocolStepDraft) -> dict[str, Any]:
    return {
        "id": step.id,
        "number": step.number,
        "title": step.title,
        "instruction": step.instruction,
        "requirements": [_requirement(item) for item in step.requirements],
        "duration_seconds": step.duration_seconds,
        "safety_notes": list(step.safety_notes),
        "source": {
            "page": step.source.page,
            "section": step.source.section,
            "excerpt": step.source.excerpt,
            "ocr_derived": step.source.ocr_derived,
        },
        "parallel_allowed": step.parallel_allowed,
        # What import could not settle and whether the reviewer acknowledged it
        # before approval; without these the export cannot show the review happened.
        "flags": [
            {
                "code": flag.code,
                "message": flag.message,
                "excerpt": flag.excerpt,
                "acknowledged": flag.acknowledged,
            }
            for flag in step.flags
        ],
    }


class NotebookExportService:
    """Read one transactionally consistent session aggregate for export."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWorkPort],
        clock: Callable[[], datetime],
        application_commit: str | None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._application_commit = application_commit.strip() if application_commit else None

    def _document(self, session_id: str) -> dict[str, Any]:
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            if session is None:
                raise NotebookExportError("SESSION_NOT_FOUND", "session was not found")
            version = uow.protocols.get_version(session.protocol_version_id)
            if version is None:
                raise NotebookExportError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            protocol = uow.protocols.get(version.protocol_id)
            if protocol is None:
                raise NotebookExportError("PROTOCOL_NOT_FOUND", "protocol was not found")
            entries = uow.notebook.list_for_session(session_id)
            inferences = uow.inferences.list_for_session(session_id)
            verifications = uow.verifications.list_for_session(session_id)
            timers = uow.timers.list_for_session(session_id)
            audit_events = uow.audit.list_for_session(session_id)
            utterances = uow.utterances.list_for_session(session_id)
            pending_actions = uow.pending_actions.list_for_session(session_id)

        return {
            "schema_name": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "exported_at": self._clock().isoformat(),
            "application_commit": self._application_commit,
            "session": {
                "id": session.id,
                "protocol_version_id": session.protocol_version_id,
                "protocol_approved": session.protocol_approved,
                "state": session.state.value,
                "steps": [
                    {
                        "step_id": item.step_id,
                        "state": item.state.value,
                        "started_at": _timestamp(item.started_at),
                        "completed_at": _timestamp(item.completed_at),
                        "skipped_reason": item.skipped_reason,
                    }
                    for item in session.steps
                ],
                "active_step_id": session.active_step_id,
                "version": session.version,
                "created_at": session.created_at.isoformat(),
                "started_at": _timestamp(session.started_at),
                "ended_at": _timestamp(session.ended_at),
                "label": session.label,
            },
            "protocol": {
                "id": protocol.id,
                "title": protocol.title,
                "created_at": protocol.created_at.isoformat(),
                "archived_at": _timestamp(protocol.archived_at),
                "version": {
                    "id": version.id,
                    "protocol_id": version.protocol_id,
                    "version_number": version.version_number,
                    "revision": version.revision,
                    "status": version.status.value,
                    "steps": [_step(item) for item in version.steps],
                    "source_sha256": version.source_sha256,
                    "source_text": version.source_text,
                    "unassigned_text": [
                        {
                            "page": item.page,
                            "section": item.section,
                            "text": item.text,
                            "reason": item.reason,
                        }
                        for item in version.unassigned_text
                    ],
                    "created_at": version.created_at.isoformat(),
                    "approved_at": _timestamp(version.approved_at),
                },
            },
            "notebook_entries": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "entry_type": item.entry_type,
                    "source": item.source.value,
                    "payload": dict(item.payload),
                    "created_at": item.created_at.isoformat(),
                    "correlation_id": item.correlation_id,
                    "supersedes_entry_id": item.supersedes_entry_id,
                    "model_inference_id": item.model_inference_id,
                }
                for item in entries
            ],
            "inferences": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "modality": item.modality,
                    "adapter": item.adapter,
                    "model_name": item.model_name,
                    "status": item.status,
                    "confidence": item.confidence,
                    "latency_ms": item.latency_ms,
                    "input_sha256": item.input_sha256,
                    "output": dict(item.output),
                    "created_at": item.created_at.isoformat(),
                }
                for item in inferences
            ],
            "reagent_verifications": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "step_id": item.step_id,
                    "requirement_id": item.requirement_id,
                    "expected_label": item.expected_label,
                    "observed_label": item.observed_label,
                    "outcome": item.outcome.value,
                    "score": item.score,
                    "expected_normalised": item.expected_normalised,
                    "observed_normalised": item.observed_normalised,
                    "match_threshold": item.match_threshold,
                    "uncertain_threshold": item.uncertain_threshold,
                    "model_confidence": item.model_confidence,
                    "decision": item.decision.value,
                    "model_inference_id": item.model_inference_id,
                    "created_at": item.created_at.isoformat(),
                    "resolved_at": _timestamp(item.resolved_at),
                }
                for item in verifications
            ],
            "timers": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "step_id": item.step_id,
                    "label": item.label,
                    "duration_seconds": item.duration_seconds,
                    "state": item.state.value,
                    "created_at": item.created_at.isoformat(),
                    "started_at": item.started_at.isoformat(),
                    "deadline_at": _timestamp(item.deadline_at),
                    "remaining_seconds_at_pause": item.remaining_seconds_at_pause,
                    "paused_at": _timestamp(item.paused_at),
                    "resolved_at": _timestamp(item.resolved_at),
                    "correlation_id": item.correlation_id,
                    "version": item.version,
                }
                for item in timers
            ],
            "audit_events": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "event_type": item.event_type,
                    "actor": item.actor,
                    "correlation_id": item.correlation_id,
                    "details": dict(item.details),
                    "created_at": item.created_at.isoformat(),
                }
                for item in audit_events
            ],
            "utterances": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "realtime_transcript": item.realtime_transcript,
                    "verification_transcript": item.verification_transcript,
                    "canonical_transcript": item.canonical_transcript,
                    "agreement": item.agreement,
                    "reasons": list(item.reasons),
                    "primary_model": item.primary_model,
                    "verification_model": item.verification_model,
                    "primary_inference_id": item.primary_inference_id,
                    "verification_inference_id": item.verification_inference_id,
                    "confirmed_transcript": item.confirmed_transcript,
                    "created_at": item.created_at.isoformat(),
                    "correlation_id": item.correlation_id,
                    "purpose": item.purpose,
                    "pending_action_id": item.pending_action_id,
                    "audio_sha256": item.audio_sha256,
                    "decision": item.decision,
                }
                for item in utterances
            ],
            "pending_actions": [
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "intent": item.intent,
                    "payload": dict(item.payload),
                    "source": item.source.value,
                    "status": item.status.value,
                    "expected_session_version": item.expected_session_version,
                    "created_at": item.created_at.isoformat(),
                    "correlation_id": item.correlation_id,
                    "model_inference_id": item.model_inference_id,
                    "resolved_at": _timestamp(item.resolved_at),
                }
                for item in pending_actions
            ],
        }

    def export_json(self, session_id: str) -> NotebookExport:
        content = json.dumps(
            self._document(session_id),
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        return NotebookExport(
            filename=f"labjarvis-{session_id}.json",
            media_type="application/json",
            content=content,
        )

    def export_csv(self, session_id: str) -> NotebookExport:
        document = self._document(session_id)
        fieldnames = [
            "export_schema_name",
            "export_schema_version",
            "exported_at",
            "protocol_version_id",
            "session_state",
            "session_label",
            "id",
            "session_id",
            "entry_type",
            "source",
            "payload",
            "created_at",
            "correlation_id",
            "supersedes_entry_id",
            "model_inference_id",
            "model_adapter",
            "model_name",
            "model_confidence",
            "model_latency_ms",
        ]
        stream = StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        inference_by_id = {item["id"]: item for item in document["inferences"]}
        for entry in document["notebook_entries"]:
            inference = inference_by_id.get(entry["model_inference_id"])
            writer.writerow(
                {
                    "export_schema_name": SCHEMA_NAME,
                    "export_schema_version": CSV_SCHEMA_VERSION,
                    "exported_at": document["exported_at"],
                    "protocol_version_id": document["session"]["protocol_version_id"],
                    "session_state": document["session"]["state"],
                    "session_label": document["session"]["label"] or "",
                    **entry,
                    "payload": json.dumps(entry["payload"], sort_keys=True),
                    "model_adapter": inference["adapter"] if inference else "",
                    "model_name": inference["model_name"] if inference else "",
                    "model_confidence": inference["confidence"] if inference else "",
                    "model_latency_ms": inference["latency_ms"] if inference else "",
                }
            )
        return NotebookExport(
            filename=f"labjarvis-{session_id}.csv",
            media_type="text/csv; charset=utf-8",
            content=stream.getvalue().encode("utf-8"),
        )
