"""Realtime session minting: deterministic fake, and hosted OpenAI.

The realtime layer is a conversational *shell*. It never receives protocol state
to remember, never decides whether an action is legal, and never holds a
long-lived credential. What it receives is:

* a short-lived grant, minted here and bound to one model and voice;
* a tool allowlist rendered from :mod:`labjarvis.application.tools`;
* instructions that state, in the strongest terms available, that it must read
  state through tools and must never assert state from memory.

None of that is a safety guarantee on its own - a model can ignore instructions.
The guarantee is that every tool call it makes is re-validated by the
application layer against the same registry, in the same transaction, with the
same confirmation policy as a button press. The instructions only reduce noise;
the gateway is what makes the arrangement safe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from labjarvis.infrastructure.hosted_models import OpenAIClient
from labjarvis.infrastructure.model_errors import ModelOutputError
from labjarvis.ports.realtime import RealtimeGrant

REALTIME_INSTRUCTIONS = """
You are the voice interface of LabJarvis, a laboratory protocol assistant used
hands-free at a bench. You are an interface, not a record.

Absolute rules:
- You do not know the protocol, the current step, the elapsed time, or any
  measurement. Call the read-only tools to find out. Never answer from memory or
  from earlier conversation, which may be stale or from another session.
- Never state that an action has happened. Tools return a result that says
  whether something was committed or is awaiting confirmation. Report exactly
  what the result says, using its wording.
- When a tool result says an action is awaiting confirmation, tell the operator
  what will be recorded and ask them to confirm. Do not treat their earlier
  phrasing as the confirmation.
- Never invent a protocol instruction, quantity, duration, or safety note. If
  the approved protocol does not say, say that it does not say.
- Repeat numbers and units back exactly as the operator said them before any
  measurement is recorded. If you are unsure of a number or a unit, ask them to
  repeat it. Never guess a decimal point.
- If a request is outside your tools, say so plainly and suggest the manual
  control instead.

Style: brief, calm, and specific. The operator has their hands full and cannot
look at the screen. Prefer one short sentence.
""".strip()


class FakeRealtimeSessionFactory:
    """A deterministic grant for fake mode, tests, and offline demonstration.

    The secret is obviously non-credential text so it can never be mistaken for
    a real one in a log, a screenshot, or a bug report.
    """

    provider_name = "deterministic-fake-realtime"

    def __init__(
        self,
        *,
        model_name: str,
        voice: str,
        transport: str,
        ttl_seconds: int,
        clock: Any = None,
    ) -> None:
        self._model_name = model_name or "fake-realtime-v1"
        self._voice = voice
        self._transport = transport
        self._ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def create_grant(
        self,
        *,
        instructions: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> RealtimeGrant:
        del instructions
        now = self._clock()
        return RealtimeGrant(
            client_secret="fake-realtime-grant-not-a-credential",
            expires_at=datetime.fromtimestamp(
                now.timestamp() + self._ttl_seconds, tz=UTC
            ),
            model_name=self._model_name,
            voice=self._voice,
            transport=self._transport,
            provider_session_id="fake-realtime-session",
            tool_names=tuple(str(tool["name"]) for tool in tools),
            metadata={"provider": self.provider_name, "billable": "false"},
        )

    def close(self) -> None:
        return None


class OpenAIRealtimeSessionFactory:
    """Mint an ephemeral OpenAI realtime client secret for one browser session.

    Uses ``POST /realtime/client_secrets``, which returns a short-lived
    ``ek_...`` value the browser exchanges for a WebRTC connection directly with
    the provider. The account key never leaves this process.
    """

    provider_name = "openai"

    def __init__(
        self,
        client: OpenAIClient,
        *,
        model_name: str,
        voice: str,
        transport: str,
        ttl_seconds: int,
    ) -> None:
        if not model_name.strip():
            raise ValueError("a realtime model identifier is required")
        self._client = client
        self._model_name = model_name
        self._voice = voice
        self._transport = transport
        self._ttl_seconds = ttl_seconds

    def create_grant(
        self,
        *,
        instructions: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> RealtimeGrant:
        payload: dict[str, Any] = {
            "expires_after": {"anchor": "created_at", "seconds": self._ttl_seconds},
            "session": {
                "type": "realtime",
                "model": self._model_name,
                "instructions": instructions,
                "output_modalities": ["audio"],
                "audio": {"output": {"voice": self._voice}},
                # The allowlist travels with the grant, so the session cannot be
                # reconfigured from the browser to call something else.
                "tools": [dict(tool) for tool in tools],
                "tool_choice": "auto",
            },
        }
        response = self._client.post_json("/realtime/client_secrets", payload)
        secret = response.data.get("value")
        if not isinstance(secret, str) or not secret:
            raise ModelOutputError("the realtime provider returned no usable client secret")
        expires_at = response.data.get("expires_at")
        if not isinstance(expires_at, int | float):
            raise ModelOutputError("the realtime provider returned no credential expiry")
        session = response.data.get("session")
        session_map: Mapping[str, Any] = session if isinstance(session, dict) else {}
        # Prefer the identifier the provider resolved over the one requested, so
        # provenance records what actually ran rather than a possibly-moving alias.
        resolved_model = session_map.get("model")
        return RealtimeGrant(
            client_secret=secret,
            expires_at=datetime.fromtimestamp(float(expires_at), tz=UTC),
            model_name=(
                resolved_model if isinstance(resolved_model, str) and resolved_model
                else self._model_name
            ),
            voice=self._voice,
            transport=self._transport,
            provider_session_id=str(session_map.get("id", "")),
            tool_names=tuple(str(tool["name"]) for tool in tools),
            metadata={
                "provider": self.provider_name,
                "request_id": response.request_id,
                "retry_count": str(response.retry_count),
            },
        )

    def close(self) -> None:
        self._client.close()
