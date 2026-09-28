"""Authoritative session discovery, restoration, and durable timer reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from labjarvis.application.errors import ApplicationError
from labjarvis.domain.models import (
    AuditEvent,
    LabSession,
    NotebookEntry,
    PendingAction,
    PendingActionStatus,
    Protocol,
    ProtocolRequirement,
    ProtocolSummary,
    ProtocolVersion,
    ReagentVerification,
    SessionState,
    SessionSummary,
    SessionTimer,
    TimerState,
    VerificationDecision,
    VerificationOutcome,
)
from labjarvis.domain.timers import remaining_seconds, settle_timer
from labjarvis.ports.repositories import UnitOfWorkPort

UowFactory = Callable[[], UnitOfWorkPort]

#: Audit event written when an operator silences timer alarms (by voice or on
#: screen). Its ``timer_ids`` detail lists every timer it silenced.
ALARM_ACKNOWLEDGED_EVENT = "timer_alarm_acknowledged"


def acknowledged_alarm_timer_ids(events: tuple[AuditEvent, ...]) -> frozenset[str]:
    """Timers whose alarm has been acknowledged, from the append-only audit trail."""

    found: set[str] = set()
    for event in events:
        if event.event_type == ALARM_ACKNOWLEDGED_EVENT:
            found.update(item for item in event.details.get("timer_ids", "").split(",") if item)
    return frozenset(found)


@dataclass(frozen=True, slots=True)
class TimerView:
    """A durable timer plus its remaining time at the authoritative read."""

    timer: SessionTimer
    remaining_seconds: int
    #: The timer has elapsed and nobody has acknowledged its alarm yet. Derived
    #: from durable state on every read, so an alarm survives a page reload.
    alarm_active: bool = False


@dataclass(frozen=True, slots=True)
class RequirementProgress:
    """Durable visible-label review state for one active-step requirement."""

    requirement_id: str
    name: str
    status: str
    verification_id: str | None


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Everything required to rebuild one session view from authority."""

    session: LabSession
    protocol: Protocol
    version: ProtocolVersion
    timers: tuple[TimerView, ...]
    pending_action: PendingAction | None
    pending_verification: ReagentVerification | None
    requirement_progress: tuple[RequirementProgress, ...]
    entries: tuple[NotebookEntry, ...]


_REQUIREMENT_STATUS_BY_DECISION = {
    VerificationDecision.PENDING: "pending_review",
    VerificationDecision.CONFIRMED: "confirmed",
    VerificationDecision.CORRECTED: "confirmed",
    VerificationDecision.REJECTED: "rejected",
    VerificationDecision.STALE: "stale",
}


def _review_status(verification: ReagentVerification) -> str:
    """The durable review state of one verification.

    Confirming a *reading* is not the same as verifying a reagent: an operator
    who confirms that the camera read "METHANOL" has confirmed a mismatch. Only a
    confirmed or corrected reading whose comparison is MATCH counts as
    "confirmed"; any other confirmed reading is "not_matching", so it can never
    look like a completed label review or silence the step-completion warning.
    """

    status = _REQUIREMENT_STATUS_BY_DECISION[verification.decision]
    if status == "confirmed" and verification.outcome is not VerificationOutcome.MATCH:
        return "not_matching"
    return status


def derive_requirement_progress(
    requirements: tuple[ProtocolRequirement, ...],
    verifications: tuple[ReagentVerification, ...],
) -> tuple[RequirementProgress, ...]:
    """Derive the latest durable label-review state in protocol order."""

    latest: dict[str, ReagentVerification] = {}
    for verification in verifications:
        if verification.requirement_id:
            latest[verification.requirement_id] = verification
    return tuple(
        RequirementProgress(
            requirement_id=requirement.requirement_id,
            name=requirement.name,
            status=(
                _review_status(latest[requirement.requirement_id])
                if requirement.requirement_id in latest
                else "unverified"
            ),
            verification_id=(
                latest[requirement.requirement_id].id
                if requirement.requirement_id in latest
                else None
            ),
        )
        for requirement in requirements
    )


class SessionOrchestrationService:
    """Own read-side session transactions and restoration repair."""

    def __init__(
        self,
        *,
        uow_factory: UowFactory,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._new_id = new_id

    @staticmethod
    def validate_discovery_range(
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> None:
        for value in (created_from, created_to):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ApplicationError(
                    "DISCOVERY_DATE_TIMEZONE_REQUIRED",
                    "discovery date filters require an explicit UTC offset",
                )
        if created_from is not None and created_to is not None and created_from > created_to:
            raise ApplicationError(
                "DISCOVERY_DATE_RANGE_INVALID",
                "discovery start date must not be after the end date",
            )

    def list_protocols(
        self,
        *,
        limit: int = 20,
        query: str | None = None,
        archived: bool = False,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[ProtocolSummary, ...]:
        self.validate_discovery_range(created_from, created_to)
        with self._uow_factory() as uow:
            return uow.protocols.list_summaries(
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
        self.validate_discovery_range(created_from, created_to)
        with self._uow_factory() as uow:
            return uow.sessions.list_summaries(
                limit=limit,
                states=states,
                query=query,
                protocol_version_id=protocol_version_id,
                label=(" ".join(label.split()) if label else None),
                created_from=created_from,
                created_to=created_to,
                protocol_archived=protocol_archived,
            )

    def get_session_snapshot(self, session_id: str) -> SessionSnapshot:
        with self._uow_factory() as uow:
            session = self._session(uow, session_id)
            version = uow.protocols.get_version(session.protocol_version_id)
            if version is None:
                raise ApplicationError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            protocol = uow.protocols.get(version.protocol_id)
            if protocol is None:  # pragma: no cover - foreign key guarantees this
                raise ApplicationError("PROTOCOL_NOT_FOUND", "protocol was not found")
            timers = self.settled_timers(uow, session_id)
            pending = uow.pending_actions.get_pending_for_session(session_id)
            if pending is not None:
                expiry_reason = self._pending_expiry_reason(session, pending)
                if expiry_reason is not None:
                    expired = replace(
                        pending,
                        status=PendingActionStatus.EXPIRED,
                        resolved_at=self._clock(),
                    )
                    uow.pending_actions.expire(expired)
                    self._append_audit(
                        uow,
                        session_id=session.id,
                        event_type="pending_action_expired",
                        correlation_id=pending.correlation_id,
                        details={"action_id": pending.id, "reason": expiry_reason},
                    )
                    pending = None
            verifications = uow.verifications.list_for_session(session_id)
            pending_verification = next(
                (
                    verification
                    for verification in verifications
                    if verification.decision is VerificationDecision.PENDING
                ),
                None,
            )
            active_step = next(
                (step for step in version.steps if step.id == session.active_step_id),
                None,
            )
            progress = derive_requirement_progress(
                active_step.requirements if active_step is not None else (),
                tuple(
                    verification
                    for verification in verifications
                    if verification.step_id == session.active_step_id
                ),
            )
            return SessionSnapshot(
                session=session,
                protocol=protocol,
                version=version,
                timers=timers,
                pending_action=pending,
                pending_verification=pending_verification,
                requirement_progress=progress,
                entries=uow.notebook.list_for_session(session_id),
            )

    def list_timers(self, session_id: str) -> tuple[TimerView, ...]:
        with self._uow_factory() as uow:
            self._session(uow, session_id)
            return self.settled_timers(uow, session_id)

    def settled_timers(
        self,
        uow: UnitOfWorkPort,
        session_id: str,
    ) -> tuple[TimerView, ...]:
        """Settle elapsed durable timers within the caller's transaction."""

        now = self._clock()
        views: list[TimerView] = []
        acknowledged = acknowledged_alarm_timer_ids(uow.audit.list_for_session(session_id))
        for timer in uow.timers.list_for_session(session_id):
            settled = settle_timer(timer, at=now)
            if settled is not None:
                uow.timers.save(settled, expected_version=timer.version)
                self._append_audit(
                    uow,
                    session_id=session_id,
                    event_type="timer_elapsed",
                    correlation_id=timer.correlation_id,
                    details={"timer_id": timer.id, "label": timer.label},
                )
                timer = settled
            views.append(
                TimerView(
                    timer=timer,
                    remaining_seconds=remaining_seconds(timer, now=now),
                    alarm_active=(
                        timer.state is TimerState.ELAPSED and timer.id not in acknowledged
                    ),
                )
            )
        return tuple(views)

    @staticmethod
    def _pending_expiry_reason(session: LabSession, action: PendingAction) -> str | None:
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
