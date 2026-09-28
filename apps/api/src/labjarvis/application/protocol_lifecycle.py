"""Immutable protocol copy and protocol-level archive lifecycle operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from hashlib import sha256

from labjarvis.domain.models import (
    AuditEvent,
    IdempotencyRecord,
    Protocol,
    ProtocolVersion,
    ProtocolVersionStatus,
)
from labjarvis.domain.protocol_validation import ProtocolValidationError, validate_protocol_steps
from labjarvis.ports.repositories import UnitOfWorkPort


class ProtocolLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProtocolLifecycleService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWorkPort],
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._new_id = new_id

    @staticmethod
    def _hash(*parts: str) -> str:
        return sha256("\x1f".join(parts).encode()).hexdigest()

    def _audit(
        self,
        uow: UnitOfWorkPort,
        *,
        event_type: str,
        correlation_id: str,
        details: dict[str, str],
    ) -> None:
        uow.audit.append(
            AuditEvent(
                id=self._new_id(),
                session_id=None,
                event_type=event_type,
                actor="operator",
                correlation_id=correlation_id,
                details=details,
                created_at=self._clock(),
            )
        )

    def copy_approved_version(
        self,
        version_id: str,
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> ProtocolVersion:
        scope = f"protocol-copy:{version_id}"
        request_hash = self._hash(version_id)
        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                if replay.request_sha256 != request_hash:
                    raise ProtocolLifecycleError(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency key was already used for different input",
                    )
                copied = uow.protocols.get_version(replay.response["version_id"])
                if copied is None:
                    raise ProtocolLifecycleError(
                        "PROTOCOL_VERSION_NOT_FOUND",
                        "copied protocol version was not found",
                    )
                return copied
            source = uow.protocols.get_version(version_id)
            if source is None:
                raise ProtocolLifecycleError(
                    "PROTOCOL_VERSION_NOT_FOUND",
                    "protocol version was not found",
                )
            if source.status is not ProtocolVersionStatus.APPROVED:
                raise ProtocolLifecycleError(
                    "PROTOCOL_VERSION_NOT_APPROVED",
                    "only an approved version can be copied",
                )
            try:
                steps = validate_protocol_steps(source.steps)
            except ProtocolValidationError as exc:
                raise ProtocolLifecycleError(exc.code, f"{exc.field}: {exc}") from exc
            copied = replace(
                source,
                id=self._new_id(),
                version_number=uow.protocols.next_version_number(source.protocol_id),
                revision=0,
                status=ProtocolVersionStatus.DRAFT,
                steps=steps,
                created_at=self._clock(),
                approved_at=None,
            )
            uow.protocols.add_version(copied)
            uow.idempotency.add(
                IdempotencyRecord(
                    scope=scope,
                    key=idempotency_key,
                    request_sha256=request_hash,
                    response={"version_id": copied.id},
                    created_at=self._clock(),
                )
            )
            self._audit(
                uow,
                event_type="protocol_version_copied",
                correlation_id=correlation_id,
                details={"source_version_id": source.id, "new_version_id": copied.id},
            )
            return copied

    def set_archived(
        self,
        protocol_id: str,
        *,
        archived: bool,
        idempotency_key: str,
        correlation_id: str,
    ) -> Protocol:
        operation = "archive" if archived else "restore"
        scope = f"protocol-{operation}:{protocol_id}"
        request_hash = self._hash(protocol_id, operation)
        with self._uow_factory() as uow:
            replay = uow.idempotency.get(scope, idempotency_key)
            if replay is not None:
                protocol = uow.protocols.get(protocol_id)
                if protocol is None:
                    raise ProtocolLifecycleError("PROTOCOL_NOT_FOUND", "protocol was not found")
                return protocol
            protocol = uow.protocols.get(protocol_id)
            if protocol is None:
                raise ProtocolLifecycleError("PROTOCOL_NOT_FOUND", "protocol was not found")
            archived_at = self._clock() if archived else None
            if (protocol.archived_at is not None) != archived:
                uow.protocols.set_archived(protocol_id, archived_at)
                protocol = replace(protocol, archived_at=archived_at)
            uow.idempotency.add(
                IdempotencyRecord(
                    scope=scope,
                    key=idempotency_key,
                    request_sha256=request_hash,
                    response={"protocol_id": protocol_id},
                    created_at=self._clock(),
                )
            )
            self._audit(
                uow,
                event_type=f"protocol_{operation}d",
                correlation_id=correlation_id,
                details={"protocol_id": protocol_id},
            )
            return protocol
