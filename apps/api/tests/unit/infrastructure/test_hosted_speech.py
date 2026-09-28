"""Hosted speech adapter behaviour over an injected transport."""

from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAISpeechToText,
)
from labjarvis.infrastructure.model_errors import ModelInputError, ModelOutputError

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"

Handler = Callable[[httpx.Request], httpx.Response]


def build_adapter(handler: Handler, *, model_name: str = "gpt-4o-transcribe") -> OpenAISpeechToText:
    config = HostedClientConfig(
        api_key=SecretStr(PLACEHOLDER_KEY),
        base_url="https://api.openai.com/v1",
        timeout_seconds=5.0,
        max_response_bytes=65536,
        max_retries=0,
    )
    client = OpenAIClient(config, transport=httpx.MockTransport(handler))
    return OpenAISpeechToText(client, model_name=model_name)


def test_posts_to_the_transcriptions_endpoint_with_the_configured_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "pause the session"})

    build_adapter(handler).transcribe(b"\x00audio", content_type="audio/wav")

    assert seen["path"] == "/v1/audio/transcriptions"
    assert str(seen["content_type"]).startswith("multipart/form-data")
    body = bytes(seen["body"])  # type: ignore[arg-type]
    assert b"gpt-4o-transcribe" in body
    assert b'filename="input.wav"' in body
    assert b"audio/wav" in body


def test_preserves_a_safe_extension_for_each_allowed_mime_type() -> None:
    for content_type, expected in (
        ("audio/webm", b'filename="input.webm"'),
        ("audio/mpeg", b'filename="input.mp3"'),
        ("audio/mp4", b'filename="input.m4a"'),
    ):
        seen: dict[str, bytes] = {}

        def handler(request: httpx.Request, store: dict[str, bytes] = seen) -> httpx.Response:
            store["body"] = request.content
            return httpx.Response(200, json={"text": "ok"})

        build_adapter(handler).transcribe(b"\x00audio", content_type=content_type)

        assert expected in seen["body"]


def test_rejects_unsupported_mime_without_contacting_the_provider() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"text": "ok"})

    adapter = build_adapter(handler)
    with pytest.raises(ModelInputError, match="unsupported audio type"):
        adapter.transcribe(b"\x00audio", content_type="application/zip")

    assert calls == 0


def test_rejects_empty_audio_without_contacting_the_provider() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"text": "ok"})

    adapter = build_adapter(handler)
    with pytest.raises(ModelInputError, match="cannot be empty"):
        adapter.transcribe(b"", content_type="audio/wav")

    assert calls == 0


def test_returns_the_transcript_verbatim_without_manufacturing_confidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "  record the volume as 5 millilitres  ",
                "usage": {"type": "duration", "seconds": 3.5},
            },
            headers={"x-request-id": "req_123"},
        )

    result = build_adapter(handler).transcribe(b"\x00audio", content_type="audio/wav")

    assert result.transcript == "record the volume as 5 millilitres"
    assert result.confidence is None
    assert result.adapter == "openai-transcriptions"
    assert result.model_name == "gpt-4o-transcribe"


def test_records_only_safe_provider_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "record the volume as 5 millilitres",
                "usage": {"type": "duration", "seconds": 3.5},
            },
            headers={"x-request-id": "req_123"},
        )

    metadata = build_adapter(handler).transcribe(b"\x00audio", content_type="audio/wav").metadata

    assert metadata["provider"] == "openai"
    assert metadata["api_operation"] == "audio.transcriptions"
    assert metadata["model_identifier"] == "gpt-4o-transcribe"
    assert metadata["request_id"] == "req_123"
    assert metadata["usage_seconds"] == "3.5"
    assert metadata["retry_count"] == "0"
    joined = " ".join(metadata.values())
    assert "record the volume" not in joined
    assert PLACEHOLDER_KEY not in joined


def test_blank_transcript_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    with pytest.raises(ModelOutputError, match="no transcript"):
        build_adapter(handler).transcribe(b"\x00audio", content_type="audio/wav")


def test_response_missing_text_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"usage": {"seconds": 1}})

    with pytest.raises(ModelOutputError, match="failed its contract"):
        build_adapter(handler).transcribe(b"\x00audio", content_type="audio/wav")


def test_readiness_probe_sends_no_audio() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"id": "gpt-4o-transcribe", "object": "model"})

    readiness = build_adapter(handler).readiness()

    assert readiness.ready is True
    assert readiness.model_name == "gpt-4o-transcribe"
    assert bodies == [b""]


def test_readiness_reports_rejected_credentials_safely() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key sk-leaked"}})

    readiness = build_adapter(handler).readiness()

    assert readiness.ready is False
    assert "credential" in readiness.detail
    assert "sk-leaked" not in readiness.detail
