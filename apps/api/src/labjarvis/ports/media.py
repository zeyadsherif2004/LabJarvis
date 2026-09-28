"""Boundary for optional raw audio and image retention."""

from dataclasses import dataclass
from typing import Protocol


class RawMediaStorageError(RuntimeError):
    """Raw media could not be retained according to the configured policy."""


@dataclass(frozen=True, slots=True)
class StoredRawMedia:
    """Non-sensitive metadata identifying a retained content-addressed blob."""

    reference: str
    sha256: str
    content_type: str
    byte_count: int


class RawMediaStorePort(Protocol):
    """Store raw media when enabled, otherwise return ``None``."""

    def store(
        self,
        *,
        session_id: str,
        modality: str,
        content_type: str,
        data: bytes,
    ) -> StoredRawMedia | None: ...

    def delete_session(self, session_id: str) -> None:
        """Remove every retained blob whose storage key belongs to a session."""
        ...
