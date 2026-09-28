"""Contract tests for OpenAI-compatible hosted providers.

Every request is served by ``httpx.MockTransport``; no real key, endpoint or
network is used. The "DeepSeek-like" and "GLM-like" cases are configurations of
the same generic adapter - base URL, model name, structured-output capability
and provider-specific request fields - exactly as an operator would set them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from labjarvis.infrastructure.cascade import CascadeTextUnderstanding
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAICompatibleLabelRecognition,
    OpenAICompatibleTextUnderstanding,
    OpenAISpeechToText,
)
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelOutputError,
    ModelUnavailableError,
)
from labjarvis.infrastructure.model_factory import build_model_adapters
from labjarvis.infrastructure.real_models import (
    EasyOcrLabelRecognition,
    FasterWhisperSpeechToText,
)
from labjarvis.ports.models import CommandContext
from labjarvis.settings import ModelMode, Settings

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"
CONTEXT = CommandContext("session-1", "ACTIVE", "step-1", "Incubate at 37 C.")
Handler = Callable[[httpx.Request], httpx.Response]


def client_for(
    handler: Handler,
    *,
    base_url: str = "https://llm.example.test/v1",
    label: str = "Example",
) -> OpenAIClient:
    return OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(PLACEHOLDER_KEY),
            base_url=base_url,
            timeout_seconds=2.0,
            max_response_bytes=65536,
            max_retries=0,
            provider="openai_compatible",
            provider_label=label,
        ),
        transport=httpx.MockTransport(handler),
    )


def chat(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 12, "total_tokens": 912},
    }


def adapter(
    handler: Handler,
    **options: Any,
) -> OpenAICompatibleTextUnderstanding:
    base_url = options.pop("base_url", "https://llm.example.test/v1")
    label = options.pop("label", "Example")
    return OpenAICompatibleTextUnderstanding(
        client_for(handler, base_url=base_url, label=label),
        model_name=options.pop("model_name", "example-chat"),
        **options,
    )


def capturing(content: str) -> tuple[Handler, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat(content))

    return handler, seen


def test_canonical_compatible_text_request_and_response() -> None:
    handler, seen = capturing('{"intent":"PAUSE_SESSION"}')
    result = adapter(handler).interpret("hold on", CONTEXT)

    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == "example-chat"
    assert seen["body"]["messages"][0]["role"] == "system"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["stream"] is False
    assert "tools" not in seen["body"]
    assert result.command.intent == "PAUSE_SESSION"
    assert result.metadata["provider"] == "openai_compatible"
    assert result.metadata["provider_label"] == "Example"
    assert result.metadata["locality"] == "hosted"
    assert result.metadata["base_domain"] == "llm.example.test"
    assert result.metadata["prompt_tokens"] == "900"
    assert PLACEHOLDER_KEY not in json.dumps(dict(result.metadata))


def test_deepseek_like_configuration_needs_no_code_change() -> None:
    handler, seen = capturing(
        '{"intent":"RECORD_MEASUREMENT","kind":"temperature","value":"37","unit":"degC"}'
    )
    result = adapter(
        handler,
        base_url="https://deepseek-like.example.test",
        label="DeepSeek",
        model_name="deepseek-like-chat",
        structured_output="json_object",
    ).interpret("log the temperature at 37 celsius", CONTEXT)

    assert seen["url"].startswith("https://deepseek-like.example.test/chat/completions")
    assert seen["body"]["model"] == "deepseek-like-chat"
    assert result.command.intent == "RECORD_MEASUREMENT"
    assert result.metadata["provider_label"] == "DeepSeek"


def test_glm_like_configuration_with_provider_specific_fields() -> None:
    handler, seen = capturing('{"intent":"SET_TIMER","duration_seconds":300}')
    result = adapter(
        handler,
        base_url="https://glm-like.example.test/api/paas/v4",
        label="GLM",
        model_name="glm-like-flash",
        extra_body={"thinking": {"type": "disabled"}},
    ).interpret("start a five minute countdown", CONTEXT)

    assert seen["path"] == "/api/paas/v4/chat/completions"
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert result.command.parameters["duration_seconds"] == "300"


def test_provider_with_json_schema_gets_the_strict_union() -> None:
    handler, seen = capturing('{"command":{"intent":"HELP"}}')
    adapter(handler, structured_output="json_schema").interpret("help", CONTEXT)

    response_format = seen["body"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert "anyOf" in json.dumps(response_format["json_schema"]["schema"])


def test_provider_without_structured_output_still_validates_strictly() -> None:
    handler, seen = capturing('Sure! {"intent":"HELP"}')

    with pytest.raises(ModelOutputError, match="command schema"):
        adapter(handler, structured_output="prompt_only").interpret("help", CONTEXT)
    assert "response_format" not in seen["body"]


def test_token_limit_field_is_configurable() -> None:
    handler, seen = capturing('{"intent":"HELP"}')
    adapter(handler, token_limit_field="max_completion_tokens").interpret("help", CONTEXT)

    assert "max_completion_tokens" in seen["body"]
    assert "max_tokens" not in seen["body"]


def test_timeout_is_reported_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(InferenceTimeoutError):
        adapter(handler).interpret("help", CONTEXT)


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failures_are_sanitised(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": f"bad key {PLACEHOLDER_KEY}"}})

    with pytest.raises(ModelUnavailableError, match="rejected the configured credential") as caught:
        adapter(handler).interpret("help", CONTEXT)
    assert PLACEHOLDER_KEY not in str(caught.value)


def test_rate_limit_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(ModelUnavailableError, match="rate limit"):
        adapter(handler).interpret("help", CONTEXT)


@pytest.mark.parametrize(
    "content",
    ["not json at all", '{"intent": "HELP"', "[]", ""],
)
def test_malformed_json_fails_closed(content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat(content))

    with pytest.raises(ModelOutputError):
        adapter(handler).interpret("help", CONTEXT)


@pytest.mark.parametrize(
    "content",
    [
        '{"intent":"DELETE_NOTEBOOK"}',
        '{"intent":"RECORD_MEASUREMENT","kind":"volume","value":"5"}',
        '{"intent":"PAUSE_SESSION","confidence":1.0}',
        '{"intent":"SET_TIMER","duration_seconds":"300"}',
    ],
)
def test_schema_invalid_output_fails_closed(content: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat(content))

    with pytest.raises(ModelOutputError, match="command schema"):
        adapter(handler).interpret("help", CONTEXT)


def test_truncated_answer_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat('{"intent":"HELP"}', finish_reason="length"))

    with pytest.raises(ModelOutputError, match="did not finish"):
        adapter(handler).interpret("help", CONTEXT)


def test_malformed_http_body_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway</html>")

    with pytest.raises(ModelOutputError, match="malformed"):
        adapter(handler).interpret("help", CONTEXT)


def test_compatible_vision_sends_an_image_part_and_reads_visible_text() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat('{"visible_text": "ETHANOL 70%"}'))

    result = OpenAICompatibleLabelRecognition(
        client_for(handler), model_name="vision-like"
    ).recognise(b"\x89PNG fake", content_type="image/png")

    parts = seen["body"]["messages"][1]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.visible_text == "ETHANOL 70%"
    assert result.confidence is None
    assert result.metadata["provider"] == "openai_compatible"


def test_compatible_speech_uses_the_transcription_surface_with_protocol_prompt() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "record the volume as 5 millilitres"})

    result = OpenAISpeechToText(client_for(handler), model_name="whisper-like").transcribe(
        b"RIFF....", content_type="audio/wav", vocabulary=("isopropanol", "millilitres")
    )

    assert seen["path"] == "/v1/audio/transcriptions"
    assert b"isopropanol" in seen["body"]
    assert result.metadata["provider"] == "openai_compatible"


# --- Configuration: per-stage providers and capability validation ----------


def base(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "model_cache_dir": tmp_path / "models",
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'x.db'}",
    }
    values.update(overrides)
    return Settings(**values)


def test_local_speech_and_vision_with_hosted_compatible_text(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        text_provider="openai_compatible",
        allow_hosted_models=True,
        hosted_text_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_text_base_url="https://deepseek-like.example.test",
        hosted_text_model_name="deepseek-like-chat",
        hosted_text_provider_label="DeepSeek",
    )
    adapters = build_model_adapters(settings)
    try:
        assert isinstance(adapters.speech, FasterWhisperSpeechToText)
        assert isinstance(adapters.labels, EasyOcrLabelRecognition)
        assert isinstance(adapters.text, CascadeTextUnderstanding)
        assert isinstance(adapters.text.language_model, OpenAICompatibleTextUnderstanding)
        assert settings.execution_profile() == "hybrid"
        assert settings.hosted_label_for("text") == "DeepSeek"
        assert settings.any_stage_hosted
    finally:
        adapters.close()


def test_hybrid_hosted_text_still_requires_explicit_opt_in(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="explicit hosted processing"):
        base(
            tmp_path,
            model_mode=ModelMode.LOCAL,
            text_provider="openai_compatible",
            hosted_text_api_key=SecretStr(PLACEHOLDER_KEY),
            hosted_text_model_name="deepseek-like-chat",
        )


def test_compatible_vision_without_declared_image_input_is_an_incompatible_modality(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="incompatible modality"):
        base(
            tmp_path,
            model_mode=ModelMode.LOCAL,
            vision_provider="openai_compatible",
            allow_hosted_models=True,
            hosted_api_key=SecretStr(PLACEHOLDER_KEY),
            hosted_vision_model_name="text-only-model",
        )


def test_compatible_speech_without_declared_transcription_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="incompatible modality"):
        base(
            tmp_path,
            model_mode=ModelMode.LOCAL,
            speech_provider="openai_compatible",
            allow_hosted_models=True,
            hosted_api_key=SecretStr(PLACEHOLDER_KEY),
            hosted_speech_model_name="whisper-like",
        )


def test_glm_like_text_and_vision_with_local_speech(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        text_provider="openai_compatible",
        vision_provider="openai_compatible",
        allow_hosted_models=True,
        hosted_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_base_url="https://glm-like.example.test/api/paas/v4",
        hosted_text_model_name="glm-like-flash",
        hosted_vision_model_name="glm-like-vision",
        hosted_vision_image_input=True,
        hosted_text_extra_body='{"thinking": {"type": "disabled"}}',
    )
    adapters = build_model_adapters(settings)
    try:
        assert isinstance(adapters.labels, OpenAICompatibleLabelRecognition)
        assert isinstance(adapters.speech, FasterWhisperSpeechToText)
    finally:
        adapters.close()


def test_extra_body_cannot_smuggle_tools_or_override_the_model(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="may not override"):
        base(tmp_path, hosted_text_extra_body='{"tools": [{"type": "function"}]}')
    with pytest.raises(ValidationError, match="JSON object"):
        base(tmp_path, hosted_text_extra_body="[1, 2]")


def test_realtime_is_refused_for_a_provider_without_realtime(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="realtime capability"):
        base(
            tmp_path,
            realtime_enabled=True,
            realtime_model_name="some-realtime",
            allow_hosted_models=True,
            hosted_api_key=SecretStr(PLACEHOLDER_KEY),
            hosted_provider="openai_compatible",
        )


def test_per_stage_keys_are_never_rendered(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        text_provider="openai_compatible",
        allow_hosted_models=True,
        hosted_text_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_text_model_name="m",
    )

    assert PLACEHOLDER_KEY not in repr(settings)
    assert PLACEHOLDER_KEY not in str(settings.model_dump())


def test_compatible_readiness_lists_models_instead_of_fetching_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "other-model"}]})

    readiness = adapter(handler).readiness()

    assert readiness.ready is False
    assert "does not list this model" in readiness.detail
