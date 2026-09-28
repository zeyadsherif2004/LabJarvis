"""Verified backup creation and staged restore application boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class BackupError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FileDigest:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: str
    created_at: datetime
    schema_revision: str
    application_commit: str | None
    profile: str
    files: tuple[FileDigest, ...]


@dataclass(frozen=True, slots=True)
class BackupPackage:
    backup_name: str
    manifest: BackupManifest


@dataclass(frozen=True, slots=True)
class RestoreValidation:
    backup_name: str
    staging_name: str
    manifest: BackupManifest
    activation_required: bool = True


class BackupManagerPort(Protocol):
    def create(self, backup_name: str) -> BackupManifest: ...

    def validate(self, backup_name: str, staging_name: str) -> BackupManifest: ...


class UnavailableBackupManager:
    def create(self, backup_name: str) -> BackupManifest:
        del backup_name
        raise BackupError(
            "BACKUP_PROFILE_UNSUPPORTED",
            "use the documented pg_dump workflow for PostgreSQL",
        )

    def validate(self, backup_name: str, staging_name: str) -> BackupManifest:
        del backup_name, staging_name
        raise BackupError(
            "BACKUP_PROFILE_UNSUPPORTED",
            "use the documented pg_restore workflow for PostgreSQL",
        )


class BackupService:
    def __init__(
        self,
        *,
        manager: BackupManagerPort,
        clock: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._new_id = new_id

    def create_backup(self) -> BackupPackage:
        timestamp = self._clock().strftime("%Y%m%d-%H%M%S")
        backup_name = f"labjarvis-{timestamp}-{self._new_id().replace('-', '')[:8]}"
        return BackupPackage(
            backup_name=backup_name,
            manifest=self._manager.create(backup_name),
        )

    def validate_restore(self, backup_name: str) -> RestoreValidation:
        staging_name = f"{backup_name}.sqlite3"
        return RestoreValidation(
            backup_name=backup_name,
            staging_name=staging_name,
            manifest=self._manager.validate(backup_name, staging_name),
        )

    def activate_restore(self) -> None:
        """Make the deliberate online/offline boundary executable and explicit."""

        raise BackupError(
            "RESTORE_ACTIVATION_OFFLINE_REQUIRED",
            "stop LabJarvis and explicitly switch configuration to the validated staging database",
        )
