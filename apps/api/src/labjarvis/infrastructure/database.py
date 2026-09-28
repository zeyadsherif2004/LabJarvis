"""SQLAlchemy persistence adapters and atomic unit of work."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

from sqlalchemy import (
    and_,
    create_engine,
    delete,
    event,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult, Engine, RowMapping, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from labjarvis.domain.models import (
    AuditEvent,
    EntrySource,
    ExtractionFlag,
    IdempotencyRecord,
    LabSession,
    ModelInference,
    NotebookEntry,
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
    SessionTimer,
    SourceReference,
    StepState,
    TimerState,
    UnassignedSourceText,
    Utterance,
    VerificationDecision,
    VerificationOutcome,
)
from labjarvis.infrastructure.db.schema import (
    audit_events_table,
    idempotency_records_table,
    metadata,
    model_inferences_table,
    notebook_entries_table,
    pending_actions_table,
    protocol_versions_table,
    protocols_table,
    reagent_verifications_table,
    session_timers_table,
    sessions_table,
    utterances_table,
)
from labjarvis.ports.repositories import RepositoryConflict


class ConcurrencyError(RuntimeError):
    """An optimistic-lock predicate did not match the stored revision."""


def _steps_json(steps: tuple[ProtocolStepDraft, ...]) -> str:
    values: list[dict[str, Any]] = []
    for step in steps:
        values.append(
            {
                "id": step.id,
                "number": step.number,
                "title": step.title,
                "instruction": step.instruction,
                "requirements": [
                    {
                        "requirement_id": requirement.requirement_id,
                        "name": requirement.name,
                        "quantity": (
                            str(requirement.quantity) if requirement.quantity is not None else None
                        ),
                        "unit": requirement.unit,
                        "aliases": list(requirement.aliases),
                    }
                    for requirement in step.requirements
                ],
                "duration_seconds": step.duration_seconds,
                "safety_notes": list(step.safety_notes),
                    "source": {
                        "page": step.source.page,
                        "section": step.source.section,
                        "excerpt": step.source.excerpt,
                        **({"ocr_derived": True} if step.source.ocr_derived else {}),
                    },
                "parallel_allowed": step.parallel_allowed,
                **(
                    {
                        "flags": [
                            {
                                "code": flag.code,
                                "message": flag.message,
                                "excerpt": flag.excerpt,
                                "acknowledged": flag.acknowledged,
                            }
                            for flag in step.flags
                        ]
                    }
                    if step.flags
                    else {}
                ),
            }
        )
    return json.dumps(values, separators=(",", ":"), sort_keys=True)


def _unassigned_json(items: tuple[UnassignedSourceText, ...]) -> str:
    return json.dumps(
        [
            {
                "page": item.page,
                "section": item.section,
                "text": item.text,
                "reason": item.reason,
            }
            for item in items
        ],
        separators=(",", ":"),
    )


def _unassigned(value: str | None) -> tuple[UnassignedSourceText, ...]:
    if not value:
        return ()
    return tuple(UnassignedSourceText(**item) for item in json.loads(value))


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _required_utc(value: datetime) -> datetime:
    restored = _utc(value)
    if restored is None:  # pragma: no cover - narrowed by the type checker
        raise ValueError("required timestamp is missing")
    return restored


def _protocol_steps(value: str) -> tuple[ProtocolStepDraft, ...]:
    raw_steps: list[dict[str, Any]] = json.loads(value)
    return tuple(
        ProtocolStepDraft(
            id=step["id"],
            number=step["number"],
            title=step["title"],
            instruction=step["instruction"],
            requirements=tuple(
                ProtocolRequirement(
                    # Rows written before requirement identity existed are
                    # backfilled by position, which reproduces the identifiers a
                    # fresh extraction would have assigned.
                    requirement_id=requirement.get("requirement_id")
                    or f"{step['id']}-req-{index}",
                    name=requirement["name"],
                    quantity=(
                        Decimal(requirement["quantity"])
                        if requirement["quantity"] is not None
                        else None
                    ),
                    unit=requirement["unit"],
                    aliases=tuple(requirement["aliases"]),
                )
                for index, requirement in enumerate(step["requirements"], start=1)
            ),
            duration_seconds=step["duration_seconds"],
            safety_notes=tuple(step["safety_notes"]),
            source=SourceReference(**step["source"]),
            parallel_allowed=step["parallel_allowed"],
            flags=tuple(ExtractionFlag(**flag) for flag in step.get("flags", ())),
        )
        for step in raw_steps
    )


def _session_steps_json(steps: tuple[SessionStep, ...]) -> str:
    return json.dumps(
        [
            {
                "step_id": step.step_id,
                "state": step.state.value,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "skipped_reason": step.skipped_reason,
            }
            for step in steps
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def _session_steps(value: str) -> tuple[SessionStep, ...]:
    rows: list[dict[str, Any]] = json.loads(value)
    legacy_waiting_states = {
        "AWAITING_INPUT",
        "AWAITING_REAGENT_VERIFICATION",
        "AWAITING_CONFIRMATION",
    }
    return tuple(
        SessionStep(
            step_id=row["step_id"],
            state=(
                StepState.IN_PROGRESS
                if row["state"] in legacy_waiting_states
                else StepState(row["state"])
            ),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            skipped_reason=row["skipped_reason"],
        )
        for row in rows
    )


class SqlAlchemyProtocolRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, protocol: Protocol, version: ProtocolVersion) -> None:
        self._session.execute(
            insert(protocols_table).values(
                id=protocol.id,
                title=protocol.title,
                created_at=protocol.created_at,
                archived_at=protocol.archived_at,
            )
        )
        self._session.execute(
            insert(protocol_versions_table).values(
                id=version.id,
                protocol_id=version.protocol_id,
                version_number=version.version_number,
                revision=version.revision,
                status=version.status.value,
                steps_json=_steps_json(version.steps),
                source_sha256=version.source_sha256,
                source_text=version.source_text,
                unassigned_json=_unassigned_json(version.unassigned_text),
                created_at=version.created_at,
                approved_at=version.approved_at,
            )
        )

    def get(self, protocol_id: str) -> Protocol | None:
        row = self._session.execute(
            select(protocols_table).where(protocols_table.c.id == protocol_id)
        ).mappings().one_or_none()
        if row is None:
            return None
        return Protocol(
            id=row["id"],
            title=row["title"],
            created_at=_required_utc(row["created_at"]),
            archived_at=_utc(row["archived_at"]),
        )

    def get_version(self, version_id: str) -> ProtocolVersion | None:
        row = self._session.execute(
            select(protocol_versions_table).where(protocol_versions_table.c.id == version_id)
        ).mappings().one_or_none()
        return _protocol_version_from_row(row) if row is not None else None

    def save_version(self, version: ProtocolVersion, *, expected_revision: int) -> None:
        if version.revision != expected_revision + 1:
            raise ConcurrencyError("protocol version revision must advance exactly once")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(protocol_versions_table)
                .where(protocol_versions_table.c.id == version.id)
                .where(protocol_versions_table.c.revision == expected_revision)
                .values(
                    revision=version.revision,
                    status=version.status.value,
                    steps_json=_steps_json(version.steps),
                    approved_at=version.approved_at,
                )
            )
        )
        if result.rowcount != 1:
            raise ConcurrencyError("protocol version changed")

    def list_summaries(
        self,
        *,
        limit: int,
        query: str | None = None,
        archived: bool = False,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[ProtocolSummary, ...]:
        """Return recent protocols with their newest version, newest first.

        Step and session counts are aggregated in SQL so the dashboard never has
        to load every version's steps just to render a list.
        """

        latest = _latest_version_subquery()
        session_counts = (
            select(
                protocol_versions_table.c.protocol_id.label("protocol_id"),
                func.count(sessions_table.c.id).label("session_count"),
            )
            .select_from(
                protocol_versions_table.join(
                    sessions_table,
                    sessions_table.c.protocol_version_id == protocol_versions_table.c.id,
                )
            )
            .group_by(protocol_versions_table.c.protocol_id)
            .subquery()
        )
        statement = (
            select(
                protocols_table.c.id,
                protocols_table.c.title,
                protocols_table.c.created_at,
                protocols_table.c.archived_at,
                protocol_versions_table.c.id.label("version_id"),
                protocol_versions_table.c.status,
                protocol_versions_table.c.revision,
                protocol_versions_table.c.steps_json,
                func.coalesce(session_counts.c.session_count, 0).label("session_count"),
            )
            .select_from(
                protocols_table.join(latest, latest.c.protocol_id == protocols_table.c.id)
                .join(
                    protocol_versions_table,
                    and_(
                        protocol_versions_table.c.protocol_id == protocols_table.c.id,
                        protocol_versions_table.c.version_number == latest.c.version_number,
                    ),
                )
                .outerjoin(
                    session_counts, session_counts.c.protocol_id == protocols_table.c.id
                )
            )
            .order_by(protocols_table.c.created_at.desc(), protocols_table.c.id.desc())
            .limit(limit)
        )
        if query:
            statement = statement.where(protocols_table.c.title.ilike(f"%{query}%"))
        statement = statement.where(
            protocols_table.c.archived_at.is_not(None)
            if archived
            else protocols_table.c.archived_at.is_(None)
        )

        if created_from is not None:
            statement = statement.where(protocols_table.c.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(protocols_table.c.created_at <= created_to)
        rows = self._session.execute(statement).mappings().all()
        return tuple(
            ProtocolSummary(
                protocol_id=row["id"],
                title=row["title"],
                created_at=_required_utc(row["created_at"]),
                latest_version_id=row["version_id"],
                latest_version_status=ProtocolVersionStatus(row["status"]),
                latest_revision=row["revision"],
                step_count=len(json.loads(row["steps_json"])),
                session_count=row["session_count"],
                archived_at=_utc(row["archived_at"]),
            )
            for row in rows
        )

    def add_version(self, version: ProtocolVersion) -> None:
        self._session.execute(
            insert(protocol_versions_table).values(
                id=version.id,
                protocol_id=version.protocol_id,
                version_number=version.version_number,
                revision=version.revision,
                status=version.status.value,
                steps_json=_steps_json(version.steps),
                source_sha256=version.source_sha256,
                source_text=version.source_text,
                unassigned_json=_unassigned_json(version.unassigned_text),
                created_at=version.created_at,
                approved_at=version.approved_at,
            )
        )

    def next_version_number(self, protocol_id: str) -> int:
        current = self._session.execute(
            select(func.max(protocol_versions_table.c.version_number)).where(
                protocol_versions_table.c.protocol_id == protocol_id
            )
        ).scalar_one()
        return int(current or 0) + 1

    def set_archived(self, protocol_id: str, archived_at: datetime | None) -> None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(protocols_table)
                .where(protocols_table.c.id == protocol_id)
                .values(archived_at=archived_at)
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("protocol archive state changed")

    def has_sessions(self, protocol_id: str) -> bool:
        count = self._session.execute(
            select(func.count(sessions_table.c.id))
            .select_from(
                protocol_versions_table.join(
                    sessions_table,
                    sessions_table.c.protocol_version_id == protocol_versions_table.c.id,
                )
            )
            .where(protocol_versions_table.c.protocol_id == protocol_id)
        ).scalar_one()
        return bool(count)

    def all_versions_are_draft(self, protocol_id: str) -> bool:
        statuses = self._session.execute(
            select(protocol_versions_table.c.status).where(
                protocol_versions_table.c.protocol_id == protocol_id
            )
        ).scalars()
        values = tuple(statuses)
        return bool(values) and all(
            value == ProtocolVersionStatus.DRAFT.value for value in values
        )

    def delete_aggregate(self, protocol_id: str) -> None:
        version_ids = tuple(
            self._session.execute(
                select(protocol_versions_table.c.id).where(
                    protocol_versions_table.c.protocol_id == protocol_id
                )
            ).scalars()
        )
        identifiers = (protocol_id, *version_ids)
        self._session.execute(
            delete(idempotency_records_table).where(
                or_(
                    *(
                        or_(
                            idempotency_records_table.c.scope.contains(identifier),
                            idempotency_records_table.c.response_json.contains(identifier),
                        )
                        for identifier in identifiers
                    )
                )
            )
        )
        self._session.execute(
            delete(audit_events_table).where(
                audit_events_table.c.session_id.is_(None),
                or_(
                    *(audit_events_table.c.details_json.contains(item) for item in identifiers)
                ),
            )
        )
        try:
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    delete(protocols_table).where(protocols_table.c.id == protocol_id)
                ),
            )
        except IntegrityError as exc:
            raise RepositoryConflict(
                "PROTOCOL_DELETE_REFERENCED",
                "protocol became referenced by a session",
            ) from exc
        if result.rowcount != 1:
            raise RepositoryConflict("PROTOCOL_NOT_FOUND", "protocol was not found")


def _latest_version_subquery() -> Any:
    """The highest version number recorded for each protocol."""

    return (
        select(
            protocol_versions_table.c.protocol_id.label("protocol_id"),
            func.max(protocol_versions_table.c.version_number).label("version_number"),
        )
        .group_by(protocol_versions_table.c.protocol_id)
        .subquery()
    )


def _protocol_version_from_row(row: RowMapping) -> ProtocolVersion:
    return ProtocolVersion(
        id=row["id"],
        protocol_id=row["protocol_id"],
        version_number=row["version_number"],
        revision=row["revision"],
        status=ProtocolVersionStatus(row["status"]),
        steps=_protocol_steps(row["steps_json"]),
        source_sha256=row["source_sha256"],
        created_at=_required_utc(row["created_at"]),
        source_text=row["source_text"],
        approved_at=_utc(row["approved_at"]),
        unassigned_text=_unassigned(row.get("unassigned_json")),
    )


class SqlAlchemySessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, session: LabSession) -> None:
        self._session.execute(insert(sessions_table).values(**_session_values(session)))

    def get(self, session_id: str) -> LabSession | None:
        row = self._session.execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        ).mappings().one_or_none()
        return _session_from_row(row) if row is not None else None

    def save(self, session: LabSession, *, expected_version: int) -> None:
        if session.version != expected_version + 1:
            raise ConcurrencyError("session version must advance exactly once")
        values = _session_values(session)
        values.pop("id")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(sessions_table)
                .where(sessions_table.c.id == session.id)
                .where(sessions_table.c.version == expected_version)
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("session version changed")

    def delete_aggregate(self, session_id: str) -> None:
        """Delete every local row owned by one session in a single transaction."""

        action_ids = tuple(
            self._session.execute(
                select(pending_actions_table.c.id).where(
                    pending_actions_table.c.session_id == session_id
                )
            ).scalars()
        )
        verification_ids = tuple(
            self._session.execute(
                select(reagent_verifications_table.c.id).where(
                    reagent_verifications_table.c.session_id == session_id
                )
            ).scalars()
        )
        identifiers = (session_id, *action_ids, *verification_ids)
        self._session.execute(
            delete(idempotency_records_table).where(
                or_(
                    *(
                        or_(
                            idempotency_records_table.c.scope.contains(identifier),
                            idempotency_records_table.c.response_json.contains(identifier),
                        )
                        for identifier in identifiers
                    )
                )
            )
        )
        for table in (
            notebook_entries_table,
            pending_actions_table,
            reagent_verifications_table,
            session_timers_table,
            utterances_table,
            audit_events_table,
            model_inferences_table,
        ):
            self._session.execute(delete(table).where(table.c.session_id == session_id))
        result = cast(
            CursorResult[Any],
            self._session.execute(delete(sessions_table).where(sessions_table.c.id == session_id)),
        )
        if result.rowcount != 1:
            raise RepositoryConflict("SESSION_NOT_FOUND", "session was not found")

    def list_summaries(
        self,
        *,
        limit: int,
        states: tuple[SessionState, ...] | None = None,
        query: str | None = None,
        protocol_version_id: str | None = None,
        label: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        protocol_archived: bool | None = None,
    ) -> tuple[SessionSummary, ...]:
        """Return recent sessions joined to their protocol, newest first."""

        rows = (
            self._session.execute(
                _session_summaries_statement(
                    limit=limit,
                    states=states,
                    query=query,
                    protocol_version_id=protocol_version_id,
                    label=label,
                    created_from=created_from,
                    created_to=created_to,
                    protocol_archived=protocol_archived,
                )
            )
            .mappings()
            .all()
        )
        return tuple(_session_summary_from_row(row) for row in rows)


def _session_summaries_statement(
    *,
    limit: int,
    states: tuple[SessionState, ...] | None,
    query: str | None,
    protocol_version_id: str | None = None,
    label: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    protocol_archived: bool | None = None,
) -> Any:
    statement = (
        select(
            sessions_table.c.id,
            sessions_table.c.protocol_version_id,
            sessions_table.c.state,
            sessions_table.c.version,
            sessions_table.c.steps_json,
            sessions_table.c.active_step_id,
            sessions_table.c.created_at,
            sessions_table.c.started_at,
            sessions_table.c.ended_at,
            sessions_table.c.label,
            protocol_versions_table.c.protocol_id,
            protocol_versions_table.c.steps_json.label("protocol_steps_json"),
            protocols_table.c.title,
        )
        .select_from(
            sessions_table.join(
                protocol_versions_table,
                protocol_versions_table.c.id == sessions_table.c.protocol_version_id,
            ).join(
                protocols_table,
                protocols_table.c.id == protocol_versions_table.c.protocol_id,
            )
        )
        .order_by(sessions_table.c.created_at.desc(), sessions_table.c.id.desc())
        .limit(limit)
    )
    if states:
        statement = statement.where(
            sessions_table.c.state.in_([state.value for state in states])
        )
    if query:
        statement = statement.where(
            or_(
                protocols_table.c.title.ilike(f"%{query}%"),
                sessions_table.c.label.ilike(f"%{query}%"),
            )
        )
    if protocol_version_id:
        # Used by the approved-protocol screen so it can offer the session that
        # actually belongs to the version in the URL, rather than whichever
        # session happened to be in memory.
        statement = statement.where(
            sessions_table.c.protocol_version_id == protocol_version_id
        )
    if label:
        statement = statement.where(sessions_table.c.label.ilike(label))
    if created_from is not None:
        statement = statement.where(sessions_table.c.created_at >= created_from)
    if created_to is not None:
        statement = statement.where(sessions_table.c.created_at <= created_to)
    if protocol_archived is not None:
        statement = statement.where(
            protocols_table.c.archived_at.is_not(None)
            if protocol_archived
            else protocols_table.c.archived_at.is_(None)
        )
    return statement


def _session_summary_from_row(row: RowMapping) -> SessionSummary:
    steps = _session_steps(row["steps_json"])
    protocol_steps: list[dict[str, Any]] = json.loads(row["protocol_steps_json"])
    numbers = {step["id"]: step["number"] for step in protocol_steps}
    completed = sum(
        1
        for step in steps
        if step.state in {StepState.COMPLETED, StepState.SKIPPED_WITH_REASON}
    )
    return SessionSummary(
        session_id=row["id"],
        protocol_id=row["protocol_id"],
        protocol_title=row["title"],
        protocol_version_id=row["protocol_version_id"],
        state=SessionState(row["state"]),
        version=row["version"],
        step_count=len(steps),
        completed_step_count=completed,
        active_step_number=numbers.get(row["active_step_id"]),
        created_at=_required_utc(row["created_at"]),
        started_at=_utc(row["started_at"]),
        ended_at=_utc(row["ended_at"]),
        label=row["label"],
    )


def _session_values(session: LabSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "protocol_version_id": session.protocol_version_id,
        "protocol_approved": session.protocol_approved,
        "state": session.state.value,
        "steps_json": _session_steps_json(session.steps),
        "active_step_id": session.active_step_id,
        "version": session.version,
        "created_at": session.created_at,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "label": session.label,
    }


def _session_from_row(row: RowMapping) -> LabSession:
    return LabSession(
        id=row["id"],
        protocol_version_id=row["protocol_version_id"],
        protocol_approved=row["protocol_approved"],
        state=SessionState(row["state"]),
        steps=_session_steps(row["steps_json"]),
        active_step_id=row["active_step_id"],
        version=row["version"],
        created_at=_required_utc(row["created_at"]),
        started_at=_utc(row["started_at"]),
        ended_at=_utc(row["ended_at"]),
        label=row["label"],
    )


class SqlAlchemyNotebookRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, entry: NotebookEntry) -> None:
        self._session.execute(
            insert(notebook_entries_table).values(
                id=entry.id,
                session_id=entry.session_id,
                entry_type=entry.entry_type,
                source=entry.source.value,
                payload_json=json.dumps(dict(entry.payload), separators=(",", ":"), sort_keys=True),
                created_at=entry.created_at,
                correlation_id=entry.correlation_id,
                supersedes_entry_id=entry.supersedes_entry_id,
                model_inference_id=entry.model_inference_id,
            )
        )

    def get(self, entry_id: str) -> NotebookEntry | None:
        row = self._session.execute(
            select(notebook_entries_table).where(notebook_entries_table.c.id == entry_id)
        ).mappings().one_or_none()
        return _notebook_entry_from_row(row) if row is not None else None

    def list_for_session(self, session_id: str) -> tuple[NotebookEntry, ...]:
        rows = self._session.execute(
            select(notebook_entries_table)
            .where(notebook_entries_table.c.session_id == session_id)
            .order_by(notebook_entries_table.c.created_at, notebook_entries_table.c.id)
        ).mappings()
        return tuple(_notebook_entry_from_row(row) for row in rows)


def _notebook_entry_from_row(row: RowMapping) -> NotebookEntry:
    return NotebookEntry(
        id=row["id"],
        session_id=row["session_id"],
        entry_type=row["entry_type"],
        source=EntrySource(row["source"]),
        payload=json.loads(row["payload_json"]),
        created_at=_required_utc(row["created_at"]),
        correlation_id=row["correlation_id"],
        supersedes_entry_id=row["supersedes_entry_id"],
        model_inference_id=row["model_inference_id"],
    )


def _pending_action_from_row(row: RowMapping) -> PendingAction:
    return PendingAction(
        id=row["id"],
        session_id=row["session_id"],
        intent=row["intent"],
        payload=json.loads(row["payload_json"]),
        source=EntrySource(row["source"]),
        status=PendingActionStatus(row["status"]),
        expected_session_version=row["expected_session_version"],
        created_at=_required_utc(row["created_at"]),
        correlation_id=row["correlation_id"],
        model_inference_id=row["model_inference_id"],
        resolved_at=_utc(row["resolved_at"]),
    )


class SqlAlchemyPendingActionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, action: PendingAction) -> None:
        try:
            self._session.execute(
                insert(pending_actions_table).values(
                    id=action.id,
                    session_id=action.session_id,
                    intent=action.intent,
                    payload_json=json.dumps(
                        dict(action.payload),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    source=action.source.value,
                    status=action.status.value,
                    expected_session_version=action.expected_session_version,
                    created_at=action.created_at,
                    correlation_id=action.correlation_id,
                    model_inference_id=action.model_inference_id,
                    resolved_at=action.resolved_at,
                )
            )
        except IntegrityError as exc:
            raise RepositoryConflict(
                "PENDING_ACTION_EXISTS",
                "the session already has an unresolved pending action",
            ) from exc

    def get(self, action_id: str) -> PendingAction | None:
        row = self._session.execute(
            select(pending_actions_table).where(pending_actions_table.c.id == action_id)
        ).mappings().one_or_none()
        return _pending_action_from_row(row) if row is not None else None

    def list_pending_for_session(self, session_id: str) -> tuple[PendingAction, ...]:
        """Unresolved proposals for a session, oldest first.

        Rehydration needs this: a reloaded tab or a reconnected realtime session
        must see the proposal that is still awaiting a decision instead of
        silently proposing the same action again.
        """

        rows = self._session.execute(
            select(pending_actions_table)
            .where(pending_actions_table.c.session_id == session_id)
            .where(pending_actions_table.c.status == PendingActionStatus.PENDING.value)
            .order_by(pending_actions_table.c.created_at, pending_actions_table.c.id)
        ).mappings().all()
        return tuple(_pending_action_from_row(row) for row in rows)

    def list_for_session(self, session_id: str) -> tuple[PendingAction, ...]:
        """Every proposal and decision for provenance export, oldest first."""

        rows = self._session.execute(
            select(pending_actions_table)
            .where(pending_actions_table.c.session_id == session_id)
            .order_by(pending_actions_table.c.created_at, pending_actions_table.c.id)
        ).mappings().all()
        return tuple(_pending_action_from_row(row) for row in rows)

    def get_pending_for_session(self, session_id: str) -> PendingAction | None:
        row = self._session.execute(
            select(pending_actions_table)
            .where(pending_actions_table.c.session_id == session_id)
            .where(pending_actions_table.c.status == PendingActionStatus.PENDING.value)
            .order_by(pending_actions_table.c.created_at, pending_actions_table.c.id)
        ).mappings().one_or_none()
        return _pending_action_from_row(row) if row is not None else None

    def resolve(self, action: PendingAction, *, expected_status: PendingActionStatus) -> None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(pending_actions_table)
                .where(pending_actions_table.c.id == action.id)
                .where(pending_actions_table.c.status == expected_status.value)
                .values(status=action.status.value, resolved_at=action.resolved_at)
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("pending action changed")

    def expire(self, action: PendingAction) -> None:
        if action.status is not PendingActionStatus.EXPIRED:
            raise ValueError("expired pending action must have EXPIRED status")
        self.resolve(action, expected_status=PendingActionStatus.PENDING)


class SqlAlchemyInferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, inference: ModelInference) -> None:
        self._session.execute(
            insert(model_inferences_table).values(
                id=inference.id,
                session_id=inference.session_id,
                modality=inference.modality,
                adapter=inference.adapter,
                model_name=inference.model_name,
                status=inference.status,
                confidence=inference.confidence,
                latency_ms=inference.latency_ms,
                input_sha256=inference.input_sha256,
                output_json=json.dumps(
                    dict(inference.output),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                created_at=inference.created_at,
            )
        )

    def list_for_session(self, session_id: str) -> tuple[ModelInference, ...]:
        rows = self._session.execute(
            select(model_inferences_table)
            .where(model_inferences_table.c.session_id == session_id)
            .order_by(model_inferences_table.c.created_at, model_inferences_table.c.id)
        ).mappings()
        return tuple(
            ModelInference(
                id=row["id"],
                session_id=row["session_id"],
                modality=row["modality"],
                adapter=row["adapter"],
                model_name=row["model_name"],
                status=row["status"],
                confidence=row["confidence"],
                latency_ms=row["latency_ms"],
                input_sha256=row["input_sha256"],
                output=json.loads(row["output_json"]),
                created_at=_required_utc(row["created_at"]),
            )
            for row in rows
        )


class SqlAlchemyVerificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, verification: ReagentVerification) -> None:
        try:
            self._session.execute(
                insert(reagent_verifications_table).values(
                    id=verification.id,
                    session_id=verification.session_id,
                    step_id=verification.step_id,
                    requirement_id=verification.requirement_id,
                    expected_label=verification.expected_label,
                    observed_label=verification.observed_label,
                    outcome=verification.outcome.value,
                    score=verification.score,
                    expected_normalised=verification.expected_normalised,
                    observed_normalised=verification.observed_normalised,
                    match_threshold=verification.match_threshold,
                    uncertain_threshold=verification.uncertain_threshold,
                    model_confidence=verification.model_confidence,
                    decision=verification.decision.value,
                    model_inference_id=verification.model_inference_id,
                    created_at=verification.created_at,
                    resolved_at=verification.resolved_at,
                )
            )
        except IntegrityError as exc:
            raise RepositoryConflict(
                "REAGENT_VERIFICATION_PENDING",
                "the requirement already has an unresolved reagent verification",
            ) from exc

    def get(self, verification_id: str) -> ReagentVerification | None:
        row = self._session.execute(
            select(reagent_verifications_table).where(
                reagent_verifications_table.c.id == verification_id
            )
        ).mappings().one_or_none()
        return _verification_from_row(row) if row is not None else None

    def get_pending(
        self,
        session_id: str,
        step_id: str,
        requirement_id: str,
    ) -> ReagentVerification | None:
        row = self._session.execute(
            select(reagent_verifications_table)
            .where(reagent_verifications_table.c.session_id == session_id)
            .where(reagent_verifications_table.c.step_id == step_id)
            .where(reagent_verifications_table.c.requirement_id == requirement_id)
            .where(reagent_verifications_table.c.decision == VerificationDecision.PENDING.value)
        ).mappings().one_or_none()
        return _verification_from_row(row) if row is not None else None

    def resolve(
        self,
        verification: ReagentVerification,
        *,
        expected_decision: VerificationDecision,
    ) -> None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(reagent_verifications_table)
                .where(reagent_verifications_table.c.id == verification.id)
                .where(reagent_verifications_table.c.decision == expected_decision.value)
                .values(
                    observed_label=verification.observed_label,
                    observed_normalised=verification.observed_normalised,
                    outcome=verification.outcome.value,
                    score=verification.score,
                    decision=verification.decision.value,
                    resolved_at=verification.resolved_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("reagent verification changed")

    def list_for_session(self, session_id: str) -> tuple[ReagentVerification, ...]:
        rows = self._session.execute(
            select(reagent_verifications_table)
            .where(reagent_verifications_table.c.session_id == session_id)
            .order_by(reagent_verifications_table.c.created_at, reagent_verifications_table.c.id)
        ).mappings()
        return tuple(_verification_from_row(row) for row in rows)


def _verification_from_row(row: RowMapping) -> ReagentVerification:
    return ReagentVerification(
        id=row["id"],
        session_id=row["session_id"],
        step_id=row["step_id"],
        requirement_id=row["requirement_id"],
        expected_label=row["expected_label"],
        observed_label=row["observed_label"],
        outcome=VerificationOutcome(row["outcome"]),
        score=row["score"],
        expected_normalised=row["expected_normalised"],
        observed_normalised=row["observed_normalised"],
        match_threshold=row["match_threshold"],
        uncertain_threshold=row["uncertain_threshold"],
        model_confidence=row["model_confidence"],
        decision=VerificationDecision(row["decision"]),
        model_inference_id=row["model_inference_id"],
        created_at=_required_utc(row["created_at"]),
        resolved_at=_utc(row["resolved_at"]),
    )


class SqlAlchemyAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: AuditEvent) -> None:
        self._session.execute(
            insert(audit_events_table).values(
                id=event.id,
                session_id=event.session_id,
                event_type=event.event_type,
                actor=event.actor,
                correlation_id=event.correlation_id,
                details_json=json.dumps(
                    dict(event.details),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                created_at=event.created_at,
            )
        )

    def list_for_session(self, session_id: str) -> tuple[AuditEvent, ...]:
        rows = self._session.execute(
            select(audit_events_table)
            .where(audit_events_table.c.session_id == session_id)
            .order_by(audit_events_table.c.created_at, audit_events_table.c.id)
        ).mappings()
        return tuple(
            AuditEvent(
                id=row["id"],
                session_id=row["session_id"],
                event_type=row["event_type"],
                actor=row["actor"],
                correlation_id=row["correlation_id"],
                details=json.loads(row["details_json"]),
                created_at=_required_utc(row["created_at"]),
            )
            for row in rows
        )


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: IdempotencyRecord) -> None:
        self._session.execute(
            insert(idempotency_records_table).values(
                scope=record.scope,
                key=record.key,
                request_sha256=record.request_sha256,
                response_json=json.dumps(
                    dict(record.response),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )

    def get(self, scope: str, key: str) -> IdempotencyRecord | None:
        row = self._session.execute(
            select(idempotency_records_table)
            .where(idempotency_records_table.c.scope == scope)
            .where(idempotency_records_table.c.key == key)
        ).mappings().one_or_none()
        if row is None:
            return None
        return IdempotencyRecord(
            scope=row["scope"],
            key=row["key"],
            request_sha256=row["request_sha256"],
            response=json.loads(row["response_json"]),
            created_at=_required_utc(row["created_at"]),
            expires_at=_utc(row["expires_at"]),
        )


def _timer_values(timer: SessionTimer) -> dict[str, Any]:
    return {
        "id": timer.id,
        "session_id": timer.session_id,
        "step_id": timer.step_id,
        "label": timer.label,
        "duration_seconds": timer.duration_seconds,
        "state": timer.state.value,
        "created_at": timer.created_at,
        "started_at": timer.started_at,
        "deadline_at": timer.deadline_at,
        "remaining_seconds_at_pause": timer.remaining_seconds_at_pause,
        "paused_at": timer.paused_at,
        "resolved_at": timer.resolved_at,
        "correlation_id": timer.correlation_id,
        "version": timer.version,
    }


def _timer_from_row(row: RowMapping) -> SessionTimer:
    return SessionTimer(
        id=row["id"],
        session_id=row["session_id"],
        step_id=row["step_id"],
        label=row["label"],
        duration_seconds=row["duration_seconds"],
        state=TimerState(row["state"]),
        created_at=_required_utc(row["created_at"]),
        started_at=_required_utc(row["started_at"]),
        deadline_at=_utc(row["deadline_at"]),
        remaining_seconds_at_pause=row["remaining_seconds_at_pause"],
        paused_at=_utc(row["paused_at"]),
        resolved_at=_utc(row["resolved_at"]),
        correlation_id=row["correlation_id"],
        version=row["version"],
    )


class SqlAlchemyTimerRepository:
    """Durable timers. Remaining time is always derived from stored timestamps."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, timer: SessionTimer) -> None:
        self._session.execute(insert(session_timers_table).values(**_timer_values(timer)))

    def get(self, timer_id: str) -> SessionTimer | None:
        row = self._session.execute(
            select(session_timers_table).where(session_timers_table.c.id == timer_id)
        ).mappings().one_or_none()
        return _timer_from_row(row) if row is not None else None

    def save(self, timer: SessionTimer, *, expected_version: int) -> None:
        if timer.version != expected_version + 1:
            raise ConcurrencyError("timer version must advance exactly once")
        values = _timer_values(timer)
        values.pop("id")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(session_timers_table)
                .where(session_timers_table.c.id == timer.id)
                .where(session_timers_table.c.version == expected_version)
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("timer version changed")

    def list_for_session(self, session_id: str) -> tuple[SessionTimer, ...]:
        rows = self._session.execute(
            select(session_timers_table)
            .where(session_timers_table.c.session_id == session_id)
            .order_by(session_timers_table.c.created_at, session_timers_table.c.id)
        ).mappings().all()
        return tuple(_timer_from_row(row) for row in rows)


class SqlAlchemyUtteranceRepository:
    """Append-only record of what was heard, by which model, and whether they agreed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, utterance: Utterance) -> None:
        self._session.execute(
            insert(utterances_table).values(
                id=utterance.id,
                session_id=utterance.session_id,
                realtime_transcript=utterance.realtime_transcript,
                verification_transcript=utterance.verification_transcript,
                canonical_transcript=utterance.canonical_transcript,
                agreement=utterance.agreement,
                reasons_json=json.dumps(list(utterance.reasons), separators=(",", ":")),
                primary_model=utterance.primary_model,
                verification_model=utterance.verification_model,
                primary_inference_id=utterance.primary_inference_id,
                verification_inference_id=utterance.verification_inference_id,
                confirmed_transcript=utterance.confirmed_transcript,
                created_at=utterance.created_at,
                correlation_id=utterance.correlation_id,
                purpose=utterance.purpose,
                pending_action_id=utterance.pending_action_id,
                audio_sha256=utterance.audio_sha256,
                decision=utterance.decision,
            )
        )

    def audio_already_used(self, session_id: str, audio_sha256: str) -> bool:
        """Whether this exact recording has already been submitted.

        A confirmation must be a new recording, not the one that produced the
        proposal replayed back. Client timestamps cannot establish that; content
        identity can.
        """

        if not audio_sha256:
            return False
        found = self._session.execute(
            select(utterances_table.c.id)
            .where(utterances_table.c.session_id == session_id)
            .where(utterances_table.c.audio_sha256 == audio_sha256)
            .limit(1)
        ).first()
        return found is not None

    def list_for_session(self, session_id: str) -> tuple[Utterance, ...]:
        rows = self._session.execute(
            select(utterances_table)
            .where(utterances_table.c.session_id == session_id)
            .order_by(utterances_table.c.created_at, utterances_table.c.id)
        ).mappings().all()
        return tuple(
            Utterance(
                id=row["id"],
                session_id=row["session_id"],
                realtime_transcript=row["realtime_transcript"],
                verification_transcript=row["verification_transcript"],
                canonical_transcript=row["canonical_transcript"],
                agreement=row["agreement"],
                reasons=tuple(json.loads(row["reasons_json"])),
                primary_model=row["primary_model"],
                verification_model=row["verification_model"],
                primary_inference_id=row["primary_inference_id"],
                verification_inference_id=row["verification_inference_id"],
                confirmed_transcript=row["confirmed_transcript"],
                purpose=row["purpose"],
                pending_action_id=row["pending_action_id"],
                audio_sha256=row["audio_sha256"],
                decision=row["decision"],
                created_at=_required_utc(row["created_at"]),
                correlation_id=row["correlation_id"],
            )
            for row in rows
        )


class SqlAlchemyUnitOfWork:
    """Own one database transaction and all repositories participating in it."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._protocols: SqlAlchemyProtocolRepository | None = None
        self._sessions: SqlAlchemySessionRepository | None = None
        self._notebook: SqlAlchemyNotebookRepository | None = None
        self._pending_actions: SqlAlchemyPendingActionRepository | None = None
        self._inferences: SqlAlchemyInferenceRepository | None = None
        self._verifications: SqlAlchemyVerificationRepository | None = None
        self._audit: SqlAlchemyAuditRepository | None = None
        self._idempotency: SqlAlchemyIdempotencyRepository | None = None
        self._timers: SqlAlchemyTimerRepository | None = None
        self._utterances: SqlAlchemyUtteranceRepository | None = None

    @property
    def protocols(self) -> SqlAlchemyProtocolRepository:
        if self._protocols is None:
            raise RuntimeError("unit of work is not active")
        return self._protocols

    @property
    def sessions(self) -> SqlAlchemySessionRepository:
        if self._sessions is None:
            raise RuntimeError("unit of work is not active")
        return self._sessions

    @property
    def notebook(self) -> SqlAlchemyNotebookRepository:
        if self._notebook is None:
            raise RuntimeError("unit of work is not active")
        return self._notebook

    @property
    def pending_actions(self) -> SqlAlchemyPendingActionRepository:
        if self._pending_actions is None:
            raise RuntimeError("unit of work is not active")
        return self._pending_actions

    @property
    def inferences(self) -> SqlAlchemyInferenceRepository:
        if self._inferences is None:
            raise RuntimeError("unit of work is not active")
        return self._inferences

    @property
    def verifications(self) -> SqlAlchemyVerificationRepository:
        if self._verifications is None:
            raise RuntimeError("unit of work is not active")
        return self._verifications

    @property
    def audit(self) -> SqlAlchemyAuditRepository:
        if self._audit is None:
            raise RuntimeError("unit of work is not active")
        return self._audit

    @property
    def idempotency(self) -> SqlAlchemyIdempotencyRepository:
        if self._idempotency is None:
            raise RuntimeError("unit of work is not active")
        return self._idempotency

    @property
    def timers(self) -> SqlAlchemyTimerRepository:
        if self._timers is None:
            raise RuntimeError("unit of work is not active")
        return self._timers

    @property
    def utterances(self) -> SqlAlchemyUtteranceRepository:
        if self._utterances is None:
            raise RuntimeError("unit of work is not active")
        return self._utterances

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work is already active")
        self._session = self._session_factory()
        self._protocols = SqlAlchemyProtocolRepository(self._session)
        self._sessions = SqlAlchemySessionRepository(self._session)
        self._notebook = SqlAlchemyNotebookRepository(self._session)
        self._pending_actions = SqlAlchemyPendingActionRepository(self._session)
        self._inferences = SqlAlchemyInferenceRepository(self._session)
        self._verifications = SqlAlchemyVerificationRepository(self._session)
        self._audit = SqlAlchemyAuditRepository(self._session)
        self._idempotency = SqlAlchemyIdempotencyRepository(self._session)
        self._timers = SqlAlchemyTimerRepository(self._session)
        self._utterances = SqlAlchemyUtteranceRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._session is None:
            return False
        try:
            if exc_type is None:
                self._session.commit()
            else:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
            self._protocols = None
            self._sessions = None
            self._notebook = None
            self._pending_actions = None
            self._inferences = None
            self._verifications = None
            self._audit = None
            self._idempotency = None
            self._timers = None
            self._utterances = None
        return False


def ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent directory of a file-backed SQLite database.

    SQLite will not create intermediate directories, so a clean checkout whose
    ``.labjarvis`` directory does not exist yet fails with a bare
    ``unable to open database file``. Both the Alembic entry point and the
    runtime session factory call this so setup order cannot matter.
    """

    if not database_url.strip():
        return
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return
    database = url.database
    if not database or database == ":memory:":
        return
    parent = Path(database).expanduser().parent
    if parent in {Path(), Path(".")}:
        return
    parent.mkdir(parents=True, exist_ok=True)


class SchemaOutOfDate(RuntimeError):
    """The database is not at the revision this build of the code expects.

    Raised at startup instead of quietly bringing the schema up with
    ``metadata.create_all()``. That is what the previous default did, and
    against a database stamped at an older revision it created current tables
    ahead of the migrations that own them, leaving the database unable to
    migrate at all.
    """


# The revision this build of the code expects the database to be at.
#
# Held here rather than read from the migration scripts because the installed
# package does not ship `alembic.ini` or the `migrations/` directory - in the
# container image they are not next to this module at all. The constant cannot
# go stale silently: `test_expected_revision_matches_the_migration_head` fails
# the suite the moment a migration is added without updating it.
EXPECTED_SCHEMA_REVISION = "0010_protocol_import_review"


def current_revision(database_url: str) -> str | None:
    """What the database says it is at, or ``None`` when it has never migrated."""

    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            from alembic.runtime.migration import MigrationContext

            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def verify_schema_is_current(database_url: str) -> None:
    """Refuse to serve a database the migrations have not brought up to date.

    Failing here is deliberately louder than the alternative. A process that
    starts against a half-migrated database writes rows the next migration then
    has to reconcile, and the operator finds out much later.
    """

    if not database_url.strip():
        return
    expected = EXPECTED_SCHEMA_REVISION
    actual = current_revision(database_url)
    if actual == expected:
        return
    remedy = (
        "Run 'python scripts/tasks.py migrate' "
        "(equivalently: python -m alembic -c apps/api/alembic.ini upgrade head)."
    )
    if actual is None:
        raise SchemaOutOfDate(
            f"The database at {make_url(database_url).render_as_string()} has not "
            f"been migrated. It must be at revision {expected} before the "
            f"application can serve it. {remedy}"
        )
    raise SchemaOutOfDate(
        f"The database is at revision {actual}, but this build requires "
        f"{expected}. The application will not modify the schema itself. {remedy}"
    )


def create_session_factory(
    database_url: str,
    *,
    create_schema: bool = False,
) -> sessionmaker[Session]:
    """Open a connection pool for an **already migrated** database.

    ``create_schema`` exists for isolated tests that build a throwaway database
    and never run Alembic. It is off by default and is not readable from the
    environment: a deployment cannot switch it on by accident, which is the
    whole point of the change.
    """

    ensure_sqlite_directory(database_url)
    engine_options: dict[str, Any] = {"future": True}
    if database_url in {"sqlite://", "sqlite+pysqlite://"}:
        engine_options.update(
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    engine = create_engine(database_url, **engine_options)
    _configure_sqlite(engine)
    if create_schema:
        metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def _configure_sqlite(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def clear_all_data(session: Session) -> None:
    """Test-only helper that respects foreign-key ordering without dropping schema."""

    for table in reversed(metadata.sorted_tables):
        session.execute(delete(table))
