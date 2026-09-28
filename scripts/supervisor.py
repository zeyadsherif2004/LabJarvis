"""The launcher side of the narrow "restart LabJarvis" protocol (ADR 0019).

Standard library only: the launcher runs before (and without) the API
package. The constants mirror ``labjarvis.infrastructure.restart``; a unit test
keeps them identical.

The supervisor never runs anything a request supplies. It only decides
whether to start the one predefined API command again, and it does so only
when the API left a marker carrying this launch's random token.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

SUPERVISOR_TOKEN_ENV = "LABJARVIS_SUPERVISOR_TOKEN"
SUPERVISOR_DIR_ENV = "LABJARVIS_SUPERVISOR_DIR"
SUPERVISOR_FILE = "supervisor.json"
RESTART_REQUEST_FILE = "restart-request"


class RestartSupervisor:
    def __init__(self, directory: Path, token: str | None = None) -> None:
        self.directory = directory
        self.token = token or secrets.token_hex(16)

    @property
    def _record(self) -> Path:
        return self.directory / SUPERVISOR_FILE

    @property
    def _request(self) -> Path:
        return self.directory / RESTART_REQUEST_FILE

    def start(self) -> dict[str, str]:
        """Record this launch and return the environment the API needs."""

        self.directory.mkdir(parents=True, exist_ok=True)
        self._request.unlink(missing_ok=True)  # a stale request must not fire
        self._record.write_text(
            json.dumps({"token": self.token, "pid": os.getpid()}), encoding="utf-8"
        )
        return {SUPERVISOR_TOKEN_ENV: self.token, SUPERVISOR_DIR_ENV: str(self.directory)}

    def consume_restart_request(self) -> bool:
        """True exactly once per genuine request from the API this launch started."""

        try:
            content = self._request.read_text(encoding="utf-8")
        except OSError:
            return False
        self._request.unlink(missing_ok=True)
        return secrets.compare_digest(content.strip(), self.token)

    def stop(self) -> None:
        for path in (self._request, self._record):
            path.unlink(missing_ok=True)
