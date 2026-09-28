"""Explicit, aggregate-level local data deletion workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol

from labjarvis.ports.media import RawMediaStorageError, RawMediaStorePort
from labjarvis.ports.repositories import RepositoryConflict, UnitOfWorkPort


class DataManagementError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeletionReceipt:
    deletion_id: str
    deleted_at: datetime
    object_type: Literal["session", "protocol"]
    object_id_hash: str
    cleanup_state: Literal["complete", "media_cleanup_required"]


class DeletionReceiptStorePort(Protocol):
    def append(self, receipt: DeletionReceipt) -> None: ...


class DataManagementService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWorkPort],
        receipt_store: DeletionReceiptStorePort,
        raw_media_store: RawMediaStorePort,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self._uow_factory = uow_factory
        self._receipts = receipt_store
        self._raw_media = raw_media_store
        self._clock = clock
        self._new_id = new_id

    def delete_session_aggregate(
        self,
        session_id: str,
        *,
        confirmation: str,
    ) -> DeletionReceipt:
        self._require_confirmation(session_id, confirmation)
        with self._uow_factory() as uow:
            if uow.sessions.get(session_id) is None:
                raise DataManagementError("SESSION_NOT_FOUND", "session was not found")
            uow.sessions.delete_aggregate(session_id)

        cleanup_state: Literal["complete", "media_cleanup_required"] = "complete"
        try:
            self._raw_media.delete_session(session_id)
        except RawMediaStorageError:
            cleanup_state = "media_cleanup_required"
        return self._write_receipt("session", session_id, cleanup_state)

    def delete_protocol_aggregate(
        self,
        protocol_id: str,
        *,
        confirmation: str,
    ) -> DeletionReceipt:
        self._require_confirmation(protocol_id, confirmation)
        try:
            with self._uow_factory() as uow:
                protocol = uow.protocols.get(protocol_id)
                if protocol is None:
                    raise DataManagementError(
                        "PROTOCOL_NOT_FOUND", "protocol was not found"
                    )
                if uow.protocols.has_sessions(protocol_id):
                    raise DataManagementError(
                        "PROTOCOL_DELETE_REFERENCED",
                        "protocols referenced by sessions cannot be deleted; archive it instead",
                    )
                if protocol.archived_at is None and not uow.protocols.all_versions_are_draft(
                    protocol_id
                ):
                    raise DataManagementError(
                        "PROTOCOL_DELETE_NOT_ELIGIBLE",
                        "only unused draft or archived unreferenced protocols can be deleted",
                    )
                uow.protocols.delete_aggregate(protocol_id)
        except RepositoryConflict as exc:
            raise DataManagementError(exc.code, str(exc)) from exc
        return self._write_receipt("protocol", protocol_id, "complete")

    @staticmethod
    def _require_confirmation(object_id: str, confirmation: str) -> None:
        if confirmation != f"DELETE {object_id}":
            raise DataManagementError(
                "DELETION_CONFIRMATION_MISMATCH",
                "type the displayed confirmation exactly",
            )

    def _write_receipt(
        self,
        object_type: Literal["session", "protocol"],
        object_id: str,
        cleanup_state: Literal["complete", "media_cleanup_required"],
    ) -> DeletionReceipt:
        receipt = DeletionReceipt(
            deletion_id=self._new_id(),
            deleted_at=self._clock(),
            object_type=object_type,
            object_id_hash=sha256(object_id.encode("utf-8")).hexdigest(),
            cleanup_state=cleanup_state,
        )
        try:
            self._receipts.append(receipt)
        except OSError as exc:
            raise DataManagementError(
                "DELETION_RECEIPT_WRITE_FAILED",
                "the aggregate was deleted but its local receipt could not be written",
            ) from exc
        return receipt
