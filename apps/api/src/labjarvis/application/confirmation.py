"""Human decision workflow for pending model or tool proposals."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from labjarvis.application.errors import ApplicationError
from labjarvis.domain.models import (
    AuditEvent,
    LabSession,
    NotebookEntry,
    PendingAction,
    PendingActionStatus,
    SessionState,
)
from labjarvis.ports.repositories import UnitOfWorkPort

UowFactory = Callable[[], UnitOfWorkPort]


class ApplyConfirmedAction(Protocol):
    def __call__(
        self,
        uow: UnitOfWorkPort,
        session: LabSession,
        action: PendingAction,
        *,
        correlation_id: str,
    ) -> tuple[LabSession, NotebookEntry]: ...


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    session: LabSession
    pending_action: PendingAction
    entry: NotebookEntry | None


class ConfirmationService:
    """Own proposal decisions while sharing one allowlisted apply callback."""

    def __init__(
        self,
        *,
        uow_factory: UowFactory,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
        apply_confirmed_action: ApplyConfirmedAction,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._new_id = new_id
        self._apply_confirmed_action = apply_confirmed_action

    def load_resolvable_action(
        self,
        uow: UnitOfWorkPort,
        action_id: str,
    ) -> tuple[PendingAction, LabSession]:
        action = uow.pending_actions.get(action_id)
        if action is None:
            raise ApplicationError("PENDING_ACTION_NOT_FOUND", "pending action was not found")
        if action.status is not PendingActionStatus.PENDING:
            raise ApplicationError(
                "PENDING_ACTION_RESOLVED",
                "pending action is already resolved",
            )
        return action, self._session(uow, action.session_id)

    def resolve_pending_within(
        self,
        uow: UnitOfWorkPort,
        action: PendingAction,
        session: LabSession,
        *,
        accepted: bool,
        correlation_id: str,
        decided_by: str,
    ) -> tuple[LabSession, PendingAction, NotebookEntry | None]:
        """Commit or discard one proposal in the caller's transaction."""

        resolved = replace(
            action,
            status=(
                PendingActionStatus.ACCEPTED if accepted else PendingActionStatus.REJECTED
            ),
            resolved_at=self._clock(),
        )
        entry: NotebookEntry | None = None
        updated_session = session
        if accepted:
            self._require_version(session, action.expected_session_version)
            updated_session, entry = self._apply_confirmed_action(
                uow,
                session,
                action,
                correlation_id=correlation_id,
            )
        uow.pending_actions.resolve(resolved, expected_status=PendingActionStatus.PENDING)
        self._append_audit(
            uow,
            session_id=session.id,
            event_type="command_confirmed" if accepted else "command_rejected_by_user",
            correlation_id=correlation_id,
            details={
                "action_id": action.id,
                "intent": action.intent,
                "decided_by": decided_by,
            },
        )
        return updated_session, resolved, entry

    def confirm_pending_action(
        self,
        action_id: str,
        *,
        accepted: bool,
        correlation_id: str,
    ) -> ConfirmationResult:
        with self._uow_factory() as uow:
            action = uow.pending_actions.get(action_id)
            if action is None:
                raise ApplicationError(
                    "PENDING_ACTION_NOT_FOUND",
                    "pending action was not found",
                )
            session = self._session(uow, action.session_id)
            requested_status = (
                PendingActionStatus.ACCEPTED if accepted else PendingActionStatus.REJECTED
            )
            if action.status is requested_status:
                return ConfirmationResult(
                    session=session,
                    pending_action=action,
                    entry=None,
                )
            if action.status is not PendingActionStatus.PENDING:
                raise ApplicationError(
                    "PENDING_ACTION_DECISION_CONFLICT",
                    "pending action was already resolved with a different decision",
                )
            updated_session, resolved, entry = self.resolve_pending_within(
                uow,
                action,
                session,
                accepted=accepted,
                correlation_id=correlation_id,
                decided_by="on_screen_control",
            )
        return ConfirmationResult(
            session=updated_session,
            pending_action=resolved,
            entry=entry,
        )

    @staticmethod
    def pending_expiry_reason(session: LabSession, action: PendingAction) -> str | None:
        if action.expected_session_version != session.version:
            return "session_version_changed"
        if session.state not in {
            SessionState.READY,
            SessionState.ACTIVE,
            SessionState.PAUSED,
        }:
            return "session_not_interactive"
        return None

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
