"""The realtime layer is a shell: bounded credentials, bounded tools, no authority.

These tests pin the boundary rather than the provider. They assert that the
browser never receives a long-lived key, that the realtime tool surface is a
strict subset, that a new or reconnected session starts from database state, and
that the feature can be switched off without breaking anything else.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from labjarvis.api.app import create_app
from labjarvis.application.tools import realtime_tool_schemas
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.infrastructure.hosted_models import HostedClientConfig, OpenAIClient
from labjarvis.infrastructure.realtime import (
    REALTIME_INSTRUCTIONS,
    FakeRealtimeSessionFactory,
    OpenAIRealtimeSessionFactory,
)
from labjarvis.settings import Settings

ACCOUNT_KEY = "sk-account-key-that-must-never-leave-the-server"


def sample_protocol() -> bytes:
    path = (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    )
    return path.read_bytes()


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "app_env": "test",
        "data_dir": tmp_path,
        "database_url": f"sqlite+pysqlite:///{(tmp_path / 'realtime.db').as_posix()}",
    }
    return Settings(**{**base, **overrides})


def _start_session(client: TestClient) -> str:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    reviewed = client.put(
        f"/api/v1/protocol-versions/{version_id}",
        headers={"If-Match": '"0"'},
        json={"steps": imported["version"]["steps"]},
    ).json()
    body = client.post(
        f"/api/v1/protocol-versions/{version_id}/approve-and-start",
        headers={"If-Match": f'"{reviewed["revision"]}"'},
    ).json()
    session = body["session"]
    client.post(
        f"/api/v1/sessions/{session['id']}/start",
        headers={"If-Match": f'"{session["version"]}"'},
    )
    return str(session["id"])


# ------------------------------------------------------------------ feature flag


def test_realtime_is_off_by_default_and_says_so_without_breaking_the_session(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path), create_schema=True)) as client:
        session_id = _start_session(client)

        response = client.post(
            "/api/v1/realtime/sessions", json={"session_id": session_id}
        )

        assert response.status_code == 409
        assert response.json()["code"] == "REALTIME_DISABLED"
        # Typed control still works with realtime unavailable.
        typed = client.post(
            f"/api/v1/sessions/{session_id}/tools/get_current_step",
            headers={"If-Match": '"1"', "Idempotency-Key": "typed-fallback"},
            json={"parameters": {}},
        )
        assert typed.status_code == 200


def test_enabling_realtime_without_a_model_identifier_is_refused_at_startup() -> None:
    """No default model is assumed, because provider aliases drift."""

    with pytest.raises(ValueError, match="LABJARVIS_REALTIME_MODEL_NAME"):
        Settings(
            app_env="test",
            realtime_enabled=True,
            allow_hosted_models=True,
            hosted_api_key=ACCOUNT_KEY,
            realtime_model_name="",
        )


def test_enabling_realtime_without_hosted_consent_is_refused() -> None:
    with pytest.raises(ValueError, match="LABJARVIS_ALLOW_HOSTED_MODELS"):
        Settings(
            app_env="test",
            realtime_enabled=True,
            allow_hosted_models=False,
            hosted_api_key=ACCOUNT_KEY,
            realtime_model_name="gpt-realtime-2.1",
        )


# ------------------------------------------------------------------ grant contents


def test_a_realtime_grant_carries_state_and_never_the_account_key(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        realtime_enabled=True,
        allow_hosted_models=True,
        hosted_api_key=ACCOUNT_KEY,
        realtime_model_name="gpt-realtime-2.1",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id = _start_session(client)

        response = client.post(
            "/api/v1/realtime/sessions", json={"session_id": session_id}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert ACCOUNT_KEY not in response.text
        assert body["model_name"] == "gpt-realtime-2.1"
        assert body["expires_at"]
        # A voice session always starts from authoritative state, never from
        # whatever a previous conversation seemed to establish.
        assert body["snapshot"]["session"]["id"] == session_id
        assert body["snapshot"]["version"]["status"] == "APPROVED"


def test_the_realtime_tool_surface_is_a_bounded_subset(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        realtime_enabled=True,
        allow_hosted_models=True,
        hosted_api_key=ACCOUNT_KEY,
        realtime_model_name="gpt-realtime-2.1",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id = _start_session(client)

        granted = client.post(
            "/api/v1/realtime/sessions", json={"session_id": session_id}
        ).json()

        exposed = set(granted["tool_names"])
        registry = {tool["name"] for tool in client.get("/api/v1/tools").json()["tools"]}
        assert exposed
        assert exposed < registry
        assert "verify_reagent" not in exposed


def test_granting_a_realtime_session_is_recorded_in_the_audit_trail(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        realtime_enabled=True,
        allow_hosted_models=True,
        hosted_api_key=ACCOUNT_KEY,
        realtime_model_name="gpt-realtime-2.1",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        session_id = _start_session(client)
        client.post("/api/v1/realtime/sessions", json={"session_id": session_id})

        events = client.get(f"/api/v1/sessions/{session_id}/notebook").json()["audit_events"]

        granted = [item for item in events if item["event_type"] == "realtime_session_granted"]
        assert len(granted) == 1
        # Provenance names the exact model, not an alias placeholder.
        assert granted[0]["details"]["model_name"] == "gpt-realtime-2.1"
        assert "client_secret" not in str(granted[0]["details"])


def test_an_unknown_session_is_refused_before_any_provider_call(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        realtime_enabled=True,
        allow_hosted_models=True,
        hosted_api_key=ACCOUNT_KEY,
        realtime_model_name="gpt-realtime-2.1",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        response = client.post(
            "/api/v1/realtime/sessions", json={"session_id": "does-not-exist"}
        )

        assert response.status_code == 404
        assert response.json()["code"] == "SESSION_NOT_FOUND"


# ------------------------------------------------------------------ instructions


def test_the_realtime_instructions_forbid_asserting_state_from_memory() -> None:
    lowered = REALTIME_INSTRUCTIONS.casefold()

    assert "never answer from memory" in lowered
    assert "never state that an action has happened" in lowered
    assert "awaiting confirmation" in lowered
    assert "never guess a decimal point" in lowered


# ------------------------------------------------------------------ fake factory


def test_the_fake_factory_issues_an_obviously_non_credential_secret() -> None:
    factory = FakeRealtimeSessionFactory(
        model_name="fake-realtime-v1",
        voice="alloy",
        transport="webrtc",
        ttl_seconds=60,
    )

    grant = factory.create_grant(instructions="x", tools=realtime_tool_schemas())

    assert "not-a-credential" in grant.client_secret
    assert grant.metadata["billable"] == "false"
    assert grant.expires_at > datetime.now(tz=UTC)


# ------------------------------------------------------------------ hosted factory


def test_the_hosted_factory_sends_the_allowlist_and_records_the_resolved_model() -> None:
    """A stubbed transport keeps this a contract test, not a paid provider call."""

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = httpx.Request(
            "POST", request.url, content=request.content
        ).content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "value": "ek_ephemeral_value",
                "expires_at": 1900000000,
                # The provider pins the alias to a dated snapshot; provenance
                # must record what actually ran.
                "session": {"id": "sess_abc", "model": "gpt-realtime-2.1-2026-02-01"},
            },
        )

    client = OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(ACCOUNT_KEY),
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            max_response_bytes=1024 * 1024,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
    )
    factory = OpenAIRealtimeSessionFactory(
        client,
        model_name="gpt-realtime-2.1",
        voice="alloy",
        transport="webrtc",
        ttl_seconds=60,
    )

    grant = factory.create_grant(
        instructions=REALTIME_INSTRUCTIONS, tools=realtime_tool_schemas()
    )

    assert captured["path"] == "/v1/realtime/client_secrets"
    assert captured["authorization"] == f"Bearer {ACCOUNT_KEY}"
    body = str(captured["body"])
    assert "get_current_step" in body
    assert "verify_reagent" not in body
    assert grant.client_secret == "ek_ephemeral_value"
    assert grant.model_name == "gpt-realtime-2.1-2026-02-01"
    assert grant.provider_session_id == "sess_abc"


def test_a_provider_response_without_a_secret_is_treated_as_a_failure() -> None:
    client = OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(ACCOUNT_KEY),
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            max_response_bytes=1024 * 1024,
            max_retries=0,
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    factory = OpenAIRealtimeSessionFactory(
        client,
        model_name="gpt-realtime-2.1",
        voice="alloy",
        transport="webrtc",
        ttl_seconds=60,
    )

    with pytest.raises(Exception, match="client secret"):
        factory.create_grant(instructions="x", tools=())


def test_a_blank_realtime_model_identifier_is_refused_by_the_factory() -> None:
    client = OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(ACCOUNT_KEY),
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            max_response_bytes=1024 * 1024,
            max_retries=0,
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(ValueError, match="realtime model identifier"):
        OpenAIRealtimeSessionFactory(
            client, model_name="  ", voice="alloy", transport="webrtc", ttl_seconds=60
        )
