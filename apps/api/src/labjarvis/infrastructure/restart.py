"""The API side of the narrow "restart LabJarvis" protocol (ADR 0019).

``python scripts/tasks.py dev`` (and ``hands-free``) run the API under a small
supervisor. The supervisor writes ``supervisor.json`` holding a random token
into its directory and passes the token and directory to the API through the
environment. To restart, the API writes that same token into
``restart-request`` and shuts itself down along the ordinary graceful path
(the one Ctrl+C takes). The supervisor sees the API exit, finds the marker,
and starts the *same predefined command* again, which reads the saved
configuration through the normal startup path.

Nothing the browser sends reaches the marker or the command: the request body
is a single confirmation flag. Without a matching supervisor there is no
restart, and the API says so rather than exiting and never coming back.

The protocol constants are duplicated in ``scripts/supervisor.py`` (which must
run without the API installed); a unit test keeps the two in step.
"""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

SUPERVISOR_TOKEN_ENV = "LABJARVIS_SUPERVISOR_TOKEN"
SUPERVISOR_DIR_ENV = "LABJARVIS_SUPERVISOR_DIR"
SUPERVISOR_FILE = "supervisor.json"
RESTART_REQUEST_FILE = "restart-request"

UNSUPERVISED_REASON = (
    "LabJarvis was started without restart supervision, so restart it manually to apply "
    "the saved configuration."
)
SUPERVISOR_GONE_REASON = (
    "The launcher that started LabJarvis is no longer supervising it, so restart it "
    "manually to apply the saved configuration."
)


@dataclass(frozen=True, slots=True)
class RestartAvailability:
    available: bool
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {"available": self.available, "reason": self.reason}


def graceful_shutdown() -> None:
    """Stop this process the way Ctrl+C does, so the lifespan shutdown runs."""

    signal.raise_signal(signal.SIGINT)


class RestartController:
    """Knows whether a supervisor will relaunch this process, and asks it to."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        shutdown: Callable[[], None] = graceful_shutdown,
    ) -> None:
        source = os.environ if environ is None else environ
        self._token = source.get(SUPERVISOR_TOKEN_ENV, "").strip()
        directory = source.get(SUPERVISOR_DIR_ENV, "").strip()
        self._directory = Path(directory) if directory else None
        self._shutdown = shutdown

    def availability(self) -> RestartAvailability:
        if not self._token or self._directory is None:
            return RestartAvailability(False, UNSUPERVISED_REASON)
        try:
            recorded = json.loads(
                (self._directory / SUPERVISOR_FILE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return RestartAvailability(False, SUPERVISOR_GONE_REASON)
        if not isinstance(recorded, dict) or recorded.get("token") != self._token:
            return RestartAvailability(False, SUPERVISOR_GONE_REASON)
        return RestartAvailability(True, None)

    def request_restart(self) -> None:
        """Leave the marker for the supervisor. The caller then calls :meth:`shutdown`."""

        if not self.availability().available or self._directory is None:
            raise RuntimeError("restart is not available")
        marker = self._directory / RESTART_REQUEST_FILE
        temporary = marker.with_name(marker.name + ".tmp")
        temporary.write_text(self._token, encoding="utf-8")
        os.replace(temporary, marker)

    def shutdown(self) -> None:
        self._shutdown()
