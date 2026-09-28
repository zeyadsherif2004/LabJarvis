"""Online SQLite backups with checksummed, separately staged restore validation."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import closing, suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import make_url

from labjarvis.application.backup import BackupError, BackupManifest, FileDigest

FORMAT_VERSION = "labjarvis.backup/1.0"


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SQLiteBackupManager:
    def __init__(
        self,
        *,
        database_url: str,
        data_dir: Path,
        backup_dir: Path,
        profile: str,
        application_commit: str | None,
    ) -> None:
        url = make_url(database_url)
        if (
            not url.drivername.startswith("sqlite")
            or not url.database
            or url.database == ":memory:"
        ):
            raise BackupError(
                "BACKUP_PROFILE_UNSUPPORTED",
                "automated backup is available only for file-backed SQLite databases",
            )
        self._database_path = Path(url.database).resolve()
        self._data_dir = data_dir.resolve()
        self._backup_dir = backup_dir.resolve()
        self._staging_dir = (data_dir / "restore-staging").resolve()
        self._profile = profile
        self._application_commit = application_commit

    def create(self, backup_name: str) -> BackupManifest:
        target = self._safe_child(self._backup_dir, backup_name)
        temporary = self._safe_child(self._backup_dir, f".{backup_name}.{uuid4().hex}.tmp")
        if target.exists():
            raise BackupError("BACKUP_ALREADY_EXISTS", "backup package already exists")
        self._backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            temporary.mkdir(mode=0o700)
            database_backup = temporary / "database.sqlite3"
            self._online_backup(database_backup)
            self._copy_retained_media(temporary)
            files = tuple(
                FileDigest(
                    path=path.relative_to(temporary).as_posix(),
                    size=path.stat().st_size,
                    sha256=file_sha256(path),
                )
                for path in sorted(temporary.rglob("*"))
                if path.is_file()
            )
            manifest = BackupManifest(
                format_version=FORMAT_VERSION,
                created_at=datetime.now(UTC),
                schema_revision=self._schema_revision(database_backup),
                application_commit=self._application_commit,
                profile=self._profile,
                files=files,
            )
            self._write_manifest(temporary / "manifest.json", manifest)
            os.replace(temporary, target)
            return manifest
        except BackupError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise BackupError("BACKUP_CREATE_FAILED", "SQLite backup could not be created") from exc
        finally:
            with suppress(OSError):
                if temporary.exists():
                    shutil.rmtree(temporary)

    def validate(self, backup_name: str, staging_name: str) -> BackupManifest:
        package = self._safe_child(self._backup_dir, backup_name)
        staging_target = self._safe_child(self._staging_dir, staging_name)
        if not package.is_dir() or package.is_symlink():
            raise BackupError("BACKUP_NOT_FOUND", "backup package was not found")
        if staging_target.exists():
            raise BackupError("RESTORE_STAGING_EXISTS", "restore staging target already exists")
        manifest = self._read_manifest(package / "manifest.json")
        if manifest.format_version != FORMAT_VERSION:
            raise BackupError("BACKUP_FORMAT_UNSUPPORTED", "backup format is unsupported")
        expected = {item.path: item for item in manifest.files}
        actual = {
            path.relative_to(package).as_posix()
            for path in package.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        if actual != set(expected):
            raise BackupError("BACKUP_FILE_SET_MISMATCH", "backup file set does not match manifest")
        for relative, item in expected.items():
            source = self._manifest_path(package, relative)
            if source.is_symlink() or source.stat().st_size != item.size:
                raise BackupError(
                    "BACKUP_CHECKSUM_MISMATCH",
                    "backup file failed integrity validation",
                )
            if file_sha256(source) != item.sha256:
                raise BackupError(
                    "BACKUP_CHECKSUM_MISMATCH",
                    "backup file failed integrity validation",
                )

        database_backup = package / "database.sqlite3"
        if self._schema_revision(database_backup) != manifest.schema_revision:
            raise BackupError(
                "BACKUP_SCHEMA_MISMATCH",
                "backup schema revision does not match manifest",
            )
        active_revision = self._schema_revision(self._database_path)
        if manifest.schema_revision != active_revision:
            raise BackupError(
                "BACKUP_SCHEMA_INCOMPATIBLE",
                "backup schema revision differs from the active application database",
            )
        self._integrity_check(database_backup)

        self._staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._safe_child(
            self._staging_dir, f".{staging_name}.{uuid4().hex}.tmp"
        )
        try:
            shutil.copy2(database_backup, temporary)
            self._integrity_check(temporary)
            os.replace(temporary, staging_target)
        except (OSError, sqlite3.Error) as exc:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise BackupError(
                "RESTORE_STAGING_FAILED", "validated backup could not be staged"
            ) from exc
        return manifest

    def _online_backup(self, target: Path) -> None:
        if not self._database_path.is_file():
            raise BackupError("BACKUP_DATABASE_NOT_FOUND", "active SQLite database was not found")
        with closing(sqlite3.connect(self._database_path)) as source, closing(
            sqlite3.connect(target)
        ) as destination:
            source.backup(destination)
        self._integrity_check(target)

    def _copy_retained_media(self, package: Path) -> None:
        source_root = self._data_dir / "raw-media"
        if not source_root.exists():
            return
        if source_root.is_symlink():
            raise BackupError("BACKUP_MEDIA_UNSAFE", "retained-media root is a symlink")
        target_root = package / "retained-media"
        for source in source_root.rglob("*"):
            if source.is_symlink():
                raise BackupError("BACKUP_MEDIA_UNSAFE", "retained media contains a symlink")
            relative = source.relative_to(source_root)
            target = target_root / relative
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(source, target)

    @staticmethod
    def _schema_revision(database: Path) -> str:
        try:
            with closing(sqlite3.connect(database)) as connection:
                present = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                ).fetchone()
                if present is None:
                    return "unversioned-test-schema"
                row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error as exc:
            raise BackupError(
                "BACKUP_DATABASE_INVALID",
                "backup is not a valid SQLite database",
            ) from exc
        return str(row[0]) if row is not None else "unversioned-test-schema"

    @staticmethod
    def _integrity_check(database: Path) -> None:
        try:
            with closing(
                sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            ) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise BackupError("BACKUP_DATABASE_INVALID", "SQLite integrity check failed") from exc
        if result != ("ok",):
            raise BackupError("BACKUP_DATABASE_INVALID", "SQLite integrity check failed")

    @staticmethod
    def _write_manifest(path: Path, manifest: BackupManifest) -> None:
        payload = {
            "application_commit": manifest.application_commit,
            "created_at": manifest.created_at.isoformat(),
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size}
                for item in manifest.files
            ],
            "format_version": manifest.format_version,
            "profile": manifest.profile,
            "schema_revision": manifest.schema_revision,
        }
        path.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        path.chmod(0o600)

    @staticmethod
    def _read_manifest(path: Path) -> BackupManifest:
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
            files = tuple(
                FileDigest(
                    path=str(item["path"]),
                    size=int(item["size"]),
                    sha256=str(item["sha256"]),
                )
                for item in raw["files"]
            )
            return BackupManifest(
                format_version=str(raw["format_version"]),
                created_at=datetime.fromisoformat(str(raw["created_at"])),
                schema_revision=str(raw["schema_revision"]),
                application_commit=(
                    None if raw["application_commit"] is None else str(raw["application_commit"])
                ),
                profile=str(raw["profile"]),
                files=files,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BackupError("BACKUP_MANIFEST_INVALID", "backup manifest is invalid") from exc

    @staticmethod
    def _safe_child(root: Path, name: str) -> Path:
        if not name or name in {".", ".."} or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in name
        ):
            raise BackupError("BACKUP_NAME_INVALID", "backup name is invalid")
        target = (root / name).resolve()
        if target.parent != root:
            raise BackupError("BACKUP_NAME_INVALID", "backup name escapes configured storage")
        return target

    @staticmethod
    def _manifest_path(package: Path, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise BackupError("BACKUP_MANIFEST_INVALID", "backup manifest path is unsafe")
        target = (package / Path(*pure.parts)).resolve()
        if not target.is_relative_to(package.resolve()):
            raise BackupError("BACKUP_MANIFEST_INVALID", "backup manifest path is unsafe")
        return target
