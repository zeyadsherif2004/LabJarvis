"""Contract for minting short-lived realtime credentials.

The browser never holds a long-lived provider key. It receives a short-lived
grant, minted server-side, that is already constrained to one model, one voice,
and one tool allowlist. A leaked grant therefore expires on its own and can do
nothing the backend did not already permit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RealtimeGrant:
    """A short-lived credential plus the configuration it was minted against.

    ``model_name`` is the resolved identifier the provider echoed back, not the
    requested alias. Recording the resolved value is what lets an evaluation
    result stay attributable after a provider alias moves on.
    """

    client_secret: str
    expires_at: datetime
    model_name: str
    voice: str
    transport: str
    provider_session_id: str
    tool_names: tuple[str, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class RealtimeSessionPort(Protocol):
    """Mints a realtime grant for one authenticated laboratory session."""

    provider_name: str

    def create_grant(
        self,
        *,
        instructions: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> RealtimeGrant: ...

    def close(self) -> None: ...
