"""Hosted label adapter behaviour and its safety boundary."""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAILabelRecognition,
)
from labjarvis.infrastructure.model_errors import ModelInputError, ModelOutputError

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"

Handler = Callable[[httpx.Request], httpx.Response]


def build_adapter(handler: Handler) -> OpenAILabelRecognition:
    config = HostedClientConfig(
        api_key=SecretStr(PLACEHOLDER_KEY),
        base_url="https://api.openai.com/v1",
        timeout_seconds=5.0,
        max_response_bytes=65536,
        max_retries=0,
    )
    client = OpenAIClient(config, transport=httpx.MockTransport(handler))
    return OpenAILabelRecognition(client, model_name="gpt-5.6-terra")


def completed(visible_text: str) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": json.dumps({"visible_text": visible_text})}
                ],
            }
        ],
        "usage": {"input_tokens": 900, "output_tokens": 6},
    }


def capture_body(image: bytes = b"\x89PNG-image", content_type: str = "image/png") -> Any:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completed("ETHANOL 70%"))

    build_adapter(handler).recognise(image, content_type=content_type)
    return seen["body"]


def test_sends_the_image_as_a_base64_data_uri() -> None:
    image = b"\x89PNG-image"
    body = capture_body(image, "image/png")
    parts = body["input"][0]["content"]
    image_part = next(part for part in parts if part["type"] == "input_image")

    expected = base64.b64encode(image).decode("ascii")
    assert image_part["image_url"] == f"data:image/png;base64,{expected}"
    assert body["model"] == "gpt-5.6-terra"


def test_accepts_exactly_the_content_types_the_local_adapter_accepts() -> None:
    for content_type in ("image/jpeg", "image/png", "image/webp"):
        assert capture_body(b"image-bytes", content_type)["model"] == "gpt-5.6-terra"


def test_rejects_unsupported_image_type_without_contacting_the_provider() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completed("ETHANOL"))

    adapter = build_adapter(handler)
    with pytest.raises(ModelInputError, match="unsupported image type"):
        adapter.recognise(b"image", content_type="image/gif")

    assert calls == 0


def test_rejects_empty_image_without_contacting_the_provider() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completed("ETHANOL"))

    adapter = build_adapter(handler)
    with pytest.raises(ModelInputError, match="cannot be empty"):
        adapter.recognise(b"", content_type="image/png")

    assert calls == 0


def test_instruction_asks_only_for_visible_text_transcription() -> None:
    instruction = capture_body()["instructions"].casefold()

    assert "visible" in instruction
    assert "do not identify the substance" in instruction
    for forbidden in ("is it safe", "suitable to use", "recommend", "diagnose"):
        assert forbidden not in instruction


def test_the_model_cannot_return_a_verification_outcome() -> None:
    schema = capture_body()["text"]["format"]["schema"]

    assert set(schema["properties"]) == {"visible_text"}
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["visible_text"]


def test_returns_visible_text_without_manufacturing_confidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completed("  ETHANOL 70%  "))

    result = build_adapter(handler).recognise(b"image", content_type="image/png")

    assert result.visible_text == "ETHANOL 70%"
    assert result.confidence is None
    assert result.adapter == "openai-responses-vision"
    assert result.metadata["provider"] == "openai"
    assert result.metadata["input_tokens"] == "900"


def test_blank_visible_text_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completed("   "))

    with pytest.raises(ModelOutputError, match="no visible text"):
        build_adapter(handler).recognise(b"image", content_type="image/png")


def test_oversized_visible_text_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completed("E" * 400))

    with pytest.raises(ModelOutputError):
        build_adapter(handler).recognise(b"image", content_type="image/png")


def test_extra_fields_in_the_label_response_are_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "resp_1",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {"visible_text": "ETHANOL", "outcome": "MATCH", "is_safe": True}
                            ),
                        }
                    ],
                }
            ],
        }
        return httpx.Response(200, json=payload)

    with pytest.raises(ModelOutputError, match="failed its contract"):
        build_adapter(handler).recognise(b"image", content_type="image/png")
