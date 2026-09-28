"""Persistence ports consumed by application use cases."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from labjarvis.domain.models import (
    AuditEvent,
    IdempotencyRecord,
    LabSession,
    ModelInference,
    NotebookEntry,
    PendingAction,
    PendingActionStatus,
    ProtocolSummary,
    ProtocolVersion,
    ReagentVerification,
    SessionState,
    SessionSummary,
    SessionTimer,
    Utterance,
    VerificationDecision,
)
from labjarvis.domain.models import Protocol as LabProtocol


class RepositoryConflict(RuntimeError):
    """A persistence invariant rejected a concurrent write."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProtocolRepository(Protocol):
    def add(self, protocol: LabProtocol, version: ProtocolVersion) -> None: ...

    def add_version(self, version: ProtocolVersion) -> None: ...

    def next_version_number(self, protocol_id: str) -> int: ...

    def set_archived(self, protocol_id: str, archived_at: datetime | None) -> None: ...

    def has_sessions(self, protocol_id: str) -> bool: ...

    def all_versions_are_draft(self, protocol_id: str) -> bool: ...

    def delete_aggregate(self, protocol_id: str) -> None: ...

    def get(self, protocol_id: str) -> LabProtocol | None: ...

    def get_version(self, version_id: str) -> ProtocolVersion | None: ...

    def save_version(self, version: ProtocolVersion, *, expected_revision: int) -> None: ...

    def list_summaries(
        self,
        *,
        limit: int,
        query: str | None = None,
        archived: bool = False,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[ProtocolSummary, ...]: ...


class SessionRepository(Protocol):
    def add(self, session: LabSession) -> None: ...

    def get(self, session_id: str) -> LabSession | None: ...

    def save(self, session: LabSession, *, expected_version: int) -> None: ...

    def delete_aggregate(self, session_id: str) -> None: ...

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
    ) -> tuple[SessionSummary, ...]: ...


class UtteranceRepository(Protocol):
    def append(self, utterance: Utterance) -> None: ...

    def list_for_session(self, session_id: str) -> tuple[Utterance, ...]: ...

    def audio_already_used(self, session_id: str, audio_sha256: str) -> bool: ...


class TimerRepository(Protocol):
    def add(self, timer: SessionTimer) -> None: ...

    def get(self, timer_id: str) -> SessionTimer | None: ...

    def save(self, timer: SessionTimer, *, expected_version: int) -> None: ...

    def list_for_session(self, session_id: str) -> tuple[SessionTimer, ...]: ...


class NotebookRepository(Protocol):
    def append(self, entry: NotebookEntry) -> None: ...

    def get(self, entry_id: str) -> NotebookEntry | None: ...

    def list_for_session(self, session_id: str) -> tuple[NotebookEntry, ...]: ...


class PendingActionRepository(Protocol):
    def add(self, action: PendingAction) -> None: ...

    def get(self, action_id: str) -> PendingAction | None: ...

    def get_pending_for_session(self, session_id: str) -> PendingAction | None: ...

    def resolve(self, action: PendingAction, *, expected_status: PendingActionStatus) -> None: ...

    def expire(self, action: PendingAction) -> None: ...

    def list_pending_for_session(self, session_id: str) -> tuple[PendingAction, ...]: ...

    def list_for_session(self, session_id: str) -> tuple[PendingAction, ...]: ...


class InferenceRepository(Protocol):
    def append(self, inference: ModelInference) -> None: ...

    def list_for_session(self, session_id: str) -> tuple[ModelInference, ...]: ...


class VerificationRepository(Protocol):
    def append(self, verification: ReagentVerification) -> None: ...

    def get(self, verification_id: str) -> ReagentVerification | None: ...

    def get_pending(
        self,
        session_id: str,
        step_id: str,
        requirement_id: str,
    ) -> ReagentVerification | None: ...

    def resolve(
        self,
        verification: ReagentVerification,
        *,
        expected_decision: VerificationDecision,
    ) -> None: ...

    def list_for_session(self, session_id: str) -> tuple[ReagentVerification, ...]: ...


class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def list_for_session(self, session_id: str) -> tuple[AuditEvent, ...]: ...


class IdempotencyRepository(Protocol):
    def add(self, record: IdempotencyRecord) -> None: ...

    def get(self, scope: str, key: str) -> IdempotencyRecord | None: ...


class UnitOfWorkPort(Protocol):
    @property
    def protocols(self) -> ProtocolRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def notebook(self) -> NotebookRepository: ...

    @property
    def pending_actions(self) -> PendingActionRepository: ...

    @property
    def inferences(self) -> InferenceRepository: ...

    @property
    def verifications(self) -> VerificationRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def idempotency(self) -> IdempotencyRepository: ...

    @property
    def timers(self) -> TimerRepository: ...

    @property
    def utterances(self) -> UtteranceRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
