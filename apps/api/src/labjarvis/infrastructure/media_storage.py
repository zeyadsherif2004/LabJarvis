"""Privacy-preserving raw media retention adapters."""

from __future__ import annotations

import os
import shutil
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from labjarvis.ports.media import RawMediaStorageError, StoredRawMedia


class DiscardRawMediaStore:
    """Default policy: process media in memory and retain no raw bytes."""

    def store(
        self,
        *,
        session_id: str,
        modality: str,
        content_type: str,
        data: bytes,
    ) -> None:
        del session_id, modality, content_type, data
        return None

    def delete_session(self, session_id: str) -> None:
        del session_id


class LocalRawMediaStore:
    """Opt-in content-addressed storage beneath the configured data directory."""

    _ALLOWED_MODALITIES: ClassVar[frozenset[str]] = frozenset({"audio", "image"})

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "raw-media"

    def store(
        self,
        *,
        session_id: str,
        modality: str,
        content_type: str,
        data: bytes,
    ) -> StoredRawMedia:
        if modality not in self._ALLOWED_MODALITIES:
            raise RawMediaStorageError("raw media modality is not allowlisted")
        if not session_id or not content_type or not data:
            raise RawMediaStorageError("raw media metadata and content are required")

        digest = sha256(data).hexdigest()
        session_digest = sha256(session_id.encode("utf-8")).hexdigest()[:32]
        directory = self._root / session_digest
        target = directory / f"{modality}-{digest}.bin"
        temporary = directory / f".{target.name}.{uuid4().hex}.tmp"

        try:
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.mkdir(exist_ok=True, mode=0o700)
            if directory.is_symlink() or not directory.resolve().is_relative_to(
                self._root.resolve()
            ):
                raise RawMediaStorageError("raw media directory is unsafe")
            if target.exists():
                if target.is_symlink() or sha256(target.read_bytes()).hexdigest() != digest:
                    raise RawMediaStorageError("retained raw media failed integrity validation")
            else:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                target.chmod(0o600)
        except RawMediaStorageError:
            raise
        except OSError as exc:
            raise RawMediaStorageError("raw media could not be written safely") from exc
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

        return StoredRawMedia(
            reference=target.relative_to(self._root.parent).as_posix(),
            sha256=digest,
            content_type=content_type,
            byte_count=len(data),
        )

    def delete_session(self, session_id: str) -> None:
        """Delete only the content-addressed directory derived from ``session_id``."""

        session_digest = sha256(session_id.encode("utf-8")).hexdigest()[:32]
        target = self._root / session_digest
        try:
            root = self._root.resolve()
            resolved = target.resolve()
            if resolved.parent != root or target.is_symlink():
                raise RawMediaStorageError("raw media deletion target is unsafe")
            if target.exists():
                shutil.rmtree(target)
        except RawMediaStorageError:
            raise
        except OSError as exc:
            raise RawMediaStorageError("retained raw media could not be deleted") from exc
