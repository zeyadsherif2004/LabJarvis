"""Hosted-profile behaviour through the real orchestrator and HTTP surface.

Every provider interaction is served by ``httpx.MockTransport``. These tests make
no external request and consume no paid provider usage.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from labjarvis.api.app import create_app
from labjarvis.infrastructure.document_ocr import UnavailableDocumentOcr
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAILabelRecognition,
    OpenAISpeechToText,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.infrastructure.model_factory import ModelAdapters
from labjarvis.ports.models import CommandContext
from labjarvis.settings import ModelMode, Settings

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"

Handler = Callable[[httpx.Request], httpx.Response]


def sample_protocol() -> bytes:
    path = (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    )
    return path.read_bytes()


def hosted_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        model_mode=ModelMode.HOSTED,
        allow_hosted_models=True,
        hosted_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_speech_model_name="gpt-4o-transcribe",
        hosted_text_model_name="gpt-5.6-terra",
        hosted_vision_model_name="gpt-5.6-terra",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'hosted-workflow.db'}",
    )


def hosted_adapters(handler: Handler, settings: Settings) -> ModelAdapters:
    client = OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(PLACEHOLDER_KEY),
            base_url=settings.hosted_base_url,
            timeout_seconds=settings.inference_timeout_seconds,
            max_response_bytes=settings.hosted_max_response_bytes,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
    )
    return ModelAdapters(
        speech=OpenAISpeechToText(client, model_name=settings.hosted_speech_model_name),
        text=OpenAITextUnderstanding(client, model_name=settings.hosted_text_model_name),
        labels=OpenAILabelRecognition(client, model_name=settings.hosted_vision_model_name),
        documents=UnavailableDocumentOcr(),
    )


def command_response(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(command)}],
            }
        ],
        "usage": {"input_tokens": 120, "output_tokens": 18},
    }


def build_hosted_app(tmp_path: Path, handler: Handler) -> tuple[Any, Settings]:
    """Create the real app, then swap in transport-backed hosted adapters."""

    settings = hosted_settings(tmp_path)
    app = create_app(settings, create_schema=True)
    app.state.model_adapters.close()
    adapters = hosted_adapters(handler, settings)
    app.state.model_adapters = adapters
    app.state.service._speech = adapters.speech
    app.state.service._text = adapters.text
    app.state.service._labels = adapters.labels
    return app, settings


def approved_session(client: TestClient) -> tuple[str, int]:
    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", sample_protocol(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    client.put(
        f"/api/v1/protocol-versions/{version_id}",
        headers={"If-Match": '"0"'},
        json={"steps": imported["version"]["steps"]},
    )
    client.post(f"/api/v1/protocol-versions/{version_id}/approve", headers={"If-Match": '"1"'})
    session = client.post("/api/v1/sessions", json={"protocol_version_id": version_id}).json()
    started = client.post(
        f"/api/v1/sessions/{session['id']}/start",
        headers={"If-Match": f'"{session["version"]}"'},
    ).json()
    return started["id"], started["version"]


# --- readiness -------------------------------------------------------------


def test_hosted_readiness_reports_external_processing_without_the_credential(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        response = client.get("/health/ready")

    payload = response.json()
    assert response.status_code == 200
    assert payload["mode"] == "hosted"
    assert payload["hosted_provider"] == "openai"
    assert payload["privacy"]["external_model_processing"] is True
    assert PLACEHOLDER_KEY not in json.dumps(payload)


def test_hosted_readiness_reports_configured_models_per_modality(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        adapters = client.get("/health/ready").json()["adapters"]

    assert adapters["speech"]["model_name"] == "gpt-4o-transcribe"
    assert adapters["text"]["adapter"] == "openai-responses-json-schema"
    assert adapters["vision"]["model_name"] == "gpt-5.6-terra"


def test_readiness_sends_no_user_content_to_the_provider(tmp_path: Path) -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        client.get("/health/ready")

    assert bodies
    assert all(body == b"" for body in bodies)


def test_invalid_hosted_credentials_fail_safely(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key sk-leaked"}})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        response = client.get("/health/ready")

    body = response.text
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "credential" in body
    assert "sk-leaked" not in body
    assert PLACEHOLDER_KEY not in body


def test_fake_readiness_reports_no_external_processing(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'fake-ready.db'}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        payload = client.get("/health/ready").json()

    assert payload["mode"] == "fake"
    assert payload["privacy"]["external_model_processing"] is False
    assert "hosted_provider" not in payload
    speech = payload["adapters"]["speech"]
    assert speech["cached"] == {
        "status": "not_applicable",
        "detail": "deterministic fixture",
    }
    assert speech["loaded"]["status"] == "not_applicable"
    assert speech["first_blocking_stage"] is None


# --- orchestration ---------------------------------------------------------


def test_hosted_text_command_produces_a_pending_action_requiring_confirmation(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json=command_response(
                    {
                        "command": {
                            "intent": "RECORD_OBSERVATION",
                            "observation": "the solution is clear",
                        }
                    }
                ),
            )
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        response = client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "hosted-1"},
            json={"transcript": "record an observation as the solution is clear"},
        )

        payload = response.json()
        assert response.status_code == 202
        assert payload["intent"] == "RECORD_OBSERVATION"
        assert payload["pending_action"]["status"] == "PENDING"

        # Nothing mutated until the operator confirms.
        assert client.get(f"/api/v1/sessions/{session_id}").json()["version"] == version


def test_hosted_audio_flows_speech_then_text_then_deterministic_validation(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "pause the session"})
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json=command_response({"command": {"intent": "PAUSE_SESSION"}}),
            )
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        response = client.post(
            f"/api/v1/sessions/{session_id}/commands/audio",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "hosted-audio-1"},
            files={"file": ("clip.wav", b"\x00audio", "audio/wav")},
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["transcript"] == "pause the session"
    assert payload["intent"] == "PAUSE_SESSION"


def test_provider_failure_leaves_session_state_untouched(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        before = client.get(f"/api/v1/sessions/{session_id}").json()

        response = client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "hosted-fail-1"},
            json={"transcript": "record an observation as clear"},
        )

        after = client.get(f"/api/v1/sessions/{session_id}").json()
        notebook = client.get(f"/api/v1/sessions/{session_id}/notebook").json()

    assert response.status_code == 503
    assert after["version"] == before["version"]
    assert after["state"] == before["state"]
    assert notebook["entries"] == []


def test_malformed_provider_output_creates_no_actionable_mutation(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json=command_response({"command": {"intent": "DELETE_EVERYTHING"}}),
            )
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        response = client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "hosted-bad-1"},
            json={"transcript": "do something unsupported"},
        )

        after = client.get(f"/api/v1/sessions/{session_id}").json()

    assert response.status_code >= 400
    assert after["version"] == version


def test_hosted_vision_output_flows_through_deterministic_classification(
    tmp_path: Path,
) -> None:
    """The model supplies visible text only; MATCH/MISMATCH stays a domain decision."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"visible_text": "Ethanol 70%"}),
                                }
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        response = client.post(
            f"/api/v1/sessions/{session_id}/reagent-verifications",
            headers={
                "If-Match": f'"{version}"',
                "Idempotency-Key": "hosted-reagent-1",
            },
            files={"file": ("label.png", b"\x89PNG-image", "image/png")},
        )

    if response.status_code == 200:
        payload = response.json()
        assert payload["observed_label"] == "Ethanol 70%"
        assert payload["outcome"] in {"MATCH", "MISMATCH", "UNCERTAIN"}
        assert payload["decision"] == "PENDING"
        # The provider exposes no calibrated OCR confidence, so none is invented.
        assert payload["model_confidence"] is None
    else:
        # The sample protocol's first step may carry no reagent requirement.
        assert response.json()["code"] == "REAGENT_REQUIREMENT_NOT_FOUND"


def test_hosted_provider_metadata_is_recorded_on_the_inference_row(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json=command_response({"command": {"intent": "PAUSE_SESSION"}}),
                headers={"x-request-id": "req_recorded"},
            )
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={"If-Match": f'"{version}"', "Idempotency-Key": "hosted-meta-1"},
            json={"transcript": "pause"},
        )
        notebook = client.get(f"/api/v1/sessions/{session_id}/notebook").json()

    inference = next(item for item in notebook["inferences"] if item["modality"] == "text")
    assert inference["adapter"] == "openai-responses-json-schema"
    assert inference["model_name"] == "gpt-5.6-terra"
    assert inference["output"]["provider"] == "openai"
    assert inference["output"]["request_id"] == "req_recorded"
    assert PLACEHOLDER_KEY not in json.dumps(notebook)


def test_hosted_idempotency_replay_is_unchanged(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/responses"):
            calls += 1
            return httpx.Response(
                200,
                json=command_response({"command": {"intent": "PAUSE_SESSION"}}),
            )
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        headers = {"If-Match": f'"{version}"', "Idempotency-Key": "hosted-replay-1"}
        body = {"transcript": "pause"}

        path = f"/api/v1/sessions/{session_id}/commands/text"
        first = client.post(path, headers=headers, json=body)
        second = client.post(path, headers=headers, json=body)

    assert first.status_code == 202
    assert second.json()["replayed"] is True
    # The replay is served from the idempotency record, not a second paid call.
    assert calls == 1


def test_stale_session_version_is_rejected_before_any_provider_call(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/responses"):
            calls += 1
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    app, _ = build_hosted_app(tmp_path, handler)
    with TestClient(app) as client:
        session_id, version = approved_session(client)
        response = client.post(
            f"/api/v1/sessions/{session_id}/commands/text",
            headers={"If-Match": f'"{version + 99}"', "Idempotency-Key": "hosted-stale-1"},
            json={"transcript": "pause"},
        )

    assert response.status_code == 409
    assert calls == 0


def test_hosted_adapters_never_reach_a_repository(tmp_path: Path) -> None:
    """The text adapter is handed a transcript and context, and nothing else."""

    adapter = hosted_adapters(
        lambda request: httpx.Response(503, json={}),
        hosted_settings(tmp_path),
    ).text
    context = CommandContext(
        session_id="session-1",
        session_state="ACTIVE",
        active_step_id=None,
        active_instruction=None,
    )

    assert not hasattr(adapter, "_uow")
    assert not hasattr(adapter, "_session_factory")
    assert not hasattr(adapter, "_repository")
    with pytest.raises(ModelUnavailableError):
        adapter.interpret("pause", context)
