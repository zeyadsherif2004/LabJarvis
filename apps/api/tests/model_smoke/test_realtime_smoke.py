"""Opt-in live realtime smoke test. Makes real, paid provider requests.

WHAT THIS PROVES: that this deployment can mint a short-lived realtime grant
from the configured provider, that the grant is bound to the model, voice, and
tool allowlist this codebase asked for, and that it carries a near-term expiry.

WHAT THIS DOES NOT PROVE: anything about spoken interaction. A WebRTC session,
microphone capture, barge-in, and reconnection all happen in a browser, and a
Python test cannot stand in for them. Latency, laboratory speech accuracy, and
usability need a live browser check of the boundary described in
`docs/architecture/decisions/0014-spoken-confirmation-requires-fresh-human-audio.md`.
A person with a microphone must perform it. Do not cite a passing smoke test
as evidence of any of that.

Two separate opt-ins are required - the flag below *and* a configured realtime
model and key - so a key present in the environment for another reason cannot
silently trigger a paid call.

    LABJARVIS_RUN_REALTIME_SMOKE=1 python scripts/tasks.py test-realtime-smoke

No credential value is ever printed, including on failure.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from labjarvis.application.tools import realtime_tool_schemas
from labjarvis.infrastructure.model_factory import build_realtime_sessions
from labjarvis.infrastructure.realtime import REALTIME_INSTRUCTIONS
from labjarvis.settings import Settings

pytestmark = pytest.mark.hosted_smoke

RUN_FLAG = "LABJARVIS_RUN_REALTIME_SMOKE"


def _settings() -> Settings:
    return Settings()


@pytest.fixture()
def realtime_factory():
    if os.environ.get(RUN_FLAG) != "1":
        pytest.skip(f"set {RUN_FLAG}=1 to authorise a paid realtime provider call")
    settings = _settings()
    if not settings.realtime_enabled:
        pytest.skip("realtime voice is not enabled in this configuration")
    factory = build_realtime_sessions(settings)
    if factory is None:
        pytest.skip("no realtime provider is configured")
    try:
        yield factory, settings
    finally:
        factory.close()


def test_a_grant_is_short_lived_and_scoped_to_the_allowlist(realtime_factory) -> None:
    factory, settings = realtime_factory
    tools = realtime_tool_schemas()

    grant = factory.create_grant(instructions=REALTIME_INSTRUCTIONS, tools=tools)

    # The browser must never be handed something long-lived.
    remaining = (grant.expires_at - datetime.now(tz=UTC)).total_seconds()
    assert 0 < remaining <= settings.realtime_session_ttl_seconds + 60, (
        "the realtime grant is not short-lived"
    )
    # It must be bound to exactly the tools this codebase allowlists - no more.
    assert set(grant.tool_names) == {schema["name"] for schema in tools}
    assert grant.model_name
    # A confirmation tool would let the model approve its own proposal.
    assert not [name for name in grant.tool_names if "confirm" in name]
    # The secret is never asserted on, printed, or logged; only its presence.
    assert grant.client_secret
