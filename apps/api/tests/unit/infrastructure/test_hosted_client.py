"""Transport-level behaviour of the hosted provider client.

Every test injects ``httpx.MockTransport``. Nothing here reaches a network or
consumes paid provider usage.
"""

from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from labjarvis.infrastructure.hosted_models import HostedClientConfig, OpenAIClient
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelOutputError,
    ModelUnavailableError,
)

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"

Handler = Callable[[httpx.Request], httpx.Response]


def build_client(handler: Handler, **overrides: object) -> OpenAIClient:
    fields: dict[str, object] = {
        "api_key": SecretStr(PLACEHOLDER_KEY),
        "base_url": "https://api.openai.com/v1",
        "timeout_seconds": 5.0,
        "max_response_bytes": 2048,
        "max_retries": 2,
    }
    fields.update(overrides)
    config = HostedClientConfig(**fields)  # type: ignore[arg-type]
    return OpenAIClient(config, transport=httpx.MockTransport(handler))


def test_sends_bearer_authorisation_to_the_configured_base_url() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    with build_client(handler) as client:
        client.post_json("/responses", {"model": "m"})

    assert seen["auth"] == f"Bearer {PLACEHOLDER_KEY}"
    assert seen["url"] == "https://api.openai.com/v1/responses"


def test_returns_decoded_json_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "resp_1", "status": "completed"})

    with build_client(handler) as client:
        payload = client.post_json("/responses", {"model": "m"})

    assert payload.data["id"] == "resp_1"


def test_extracts_request_id_case_insensitively() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"X-Request-Id": "req_abc"})

    with build_client(handler) as client:
        payload = client.post_json("/responses", {"model": "m"})

    assert payload.request_id == "req_abc"


def test_missing_request_id_is_tolerated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with build_client(handler) as client:
        payload = client.post_json("/responses", {"model": "m"})

    assert payload.request_id == ""


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_maps_to_unavailable(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "Incorrect API key sk-leaked"}})

    with build_client(handler) as client, pytest.raises(ModelUnavailableError) as caught:
        client.post_json("/responses", {"model": "m"})

    message = str(caught.value)
    assert PLACEHOLDER_KEY not in message
    assert "sk-leaked" not in message
    assert "credential" in message


def test_missing_model_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "no such model"}})

    with build_client(handler) as client, pytest.raises(ModelUnavailableError, match="not availab"):
        client.post_json("/responses", {"model": "m"})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_never_retried(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={})

    with build_client(handler) as client, pytest.raises(ModelUnavailableError):
        client.post_json("/responses", {"model": "m"})

    assert calls == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_failures_retry_up_to_the_ceiling(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={})

    with build_client(handler) as client, pytest.raises(ModelUnavailableError):
        client.post_json("/responses", {"model": "m"})

    assert calls == 3


def test_retry_ceiling_is_configurable_and_respected() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={})

    with build_client(handler, max_retries=0) as client, pytest.raises(ModelUnavailableError):
        client.post_json("/responses", {"model": "m"})

    assert calls == 1


def test_a_retry_that_succeeds_reports_its_attempt_count() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"ok": True})

    with build_client(handler) as client:
        payload = client.post_json("/responses", {"model": "m"})

    assert payload.retry_count == 1


def test_connection_failure_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with build_client(handler) as client, pytest.raises(ModelUnavailableError, match="reachable"):
        client.post_json("/responses", {"model": "m"})


def test_timeout_maps_to_inference_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("deadline", request=request)

    with build_client(handler) as client, pytest.raises(InferenceTimeoutError):
        client.post_json("/responses", {"model": "m"})


def test_oversized_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"padding":"' + b"x" * 8192 + b'"}')

    with build_client(handler) as client, pytest.raises(ModelOutputError, match="too large"):
        client.post_json("/responses", {"model": "m"})


def test_malformed_json_is_rejected_as_output_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"this is not json")

    with build_client(handler) as client, pytest.raises(ModelOutputError):
        client.post_json("/responses", {"model": "m"})


def test_non_object_json_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    with build_client(handler) as client, pytest.raises(ModelOutputError):
        client.post_json("/responses", {"model": "m"})


def test_error_messages_never_echo_the_request_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with build_client(handler) as client, pytest.raises(ModelUnavailableError) as caught:
        client.post_json("/responses", {"transcript": "confidential protocol text"})

    assert "confidential protocol text" not in str(caught.value)


def test_get_model_reports_availability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url).endswith("/models/gpt-5.6-terra")
        assert request.content == b""
        return httpx.Response(200, json={"id": "gpt-5.6-terra", "object": "model"})

    with build_client(handler) as client:
        assert client.get_model("gpt-5.6-terra").data["id"] == "gpt-5.6-terra"


def test_multipart_upload_sets_the_expected_parts() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hello"})

    with build_client(handler) as client:
        client.post_multipart(
            "/audio/transcriptions",
            files={"file": ("input.wav", b"\x00audio", "audio/wav")},
            data={"model": "gpt-4o-transcribe"},
        )

    assert str(seen["content_type"]).startswith("multipart/form-data")
    assert b"gpt-4o-transcribe" in bytes(seen["body"])  # type: ignore[arg-type]


def test_close_is_idempotent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = build_client(handler)
    client.close()
    client.close()

    assert client.is_closed
