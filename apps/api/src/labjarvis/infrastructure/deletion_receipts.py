"""Content-free deletion receipts stored outside deleted aggregates."""

from __future__ import annotations

import json
import os
from pathlib import Path

from labjarvis.application.data_management import DeletionReceipt


class LocalDeletionReceiptStore:
    def __init__(self, data_dir: Path) -> None:
        self._directory = data_dir / "deletion-receipts"
        self._path = self._directory / "receipts.jsonl"

    def append(self, receipt: DeletionReceipt) -> None:
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._directory.is_symlink():
            raise OSError("deletion receipt directory is unsafe")
        payload = json.dumps(
            {
                "cleanup_state": receipt.cleanup_state,
                "deleted_at": receipt.deleted_at.isoformat(),
                "deletion_id": receipt.deletion_id,
                "object_id_hash": receipt.object_id_hash,
                "object_type": receipt.object_type,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        descriptor = os.open(
            self._path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._path.chmod(0o600)
