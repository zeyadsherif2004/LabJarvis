"""Reasoning effort, loopback locality, and the provider-independent verifier.

All provider traffic is served by ``httpx.MockTransport``; no key or network.
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
    OpenAICompatibleTextUnderstanding,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.model_errors import ModelOutputError, ModelUnavailableError
from labjarvis.infrastructure.model_factory import build_model_adapters, build_verifier
from labjarvis.infrastructure.real_models import OllamaTextUnderstanding
from labjarvis.ports.models import CommandContext
from labjarvis.settings import ModelMode, Settings

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"
CONTEXT = CommandContext("session-1", "ACTIVE", "step-1", "Incubate at 37 C.")
Handler = Callable[[httpx.Request], httpx.Response]


def openai_client(handler: Handler, *, base_url: str = "https://api.openai.com/v1") -> OpenAIClient:
    return OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(PLACEHOLDER_KEY),
            base_url=base_url,
            timeout_seconds=2.0,
            max_response_bytes=65536,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
    )


def compatible_client(handler: Handler, *, base_url: str) -> OpenAIClient:
    return OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(PLACEHOLDER_KEY),
            base_url=base_url,
            timeout_seconds=2.0,
            max_response_bytes=65536,
            max_retries=0,
            provider="openai_compatible",
            provider_label="Local server",
        ),
        transport=httpx.MockTransport(handler),
    )


def responses_payload(command: dict[str, Any], *, reasoning: bool = True) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if reasoning:
        # Reasoning models emit a reasoning item before the message.
        output.append({"type": "reasoning", "id": "rs_1", "summary": []})
    output.append(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(command)}],
        }
    )
    return {
        "id": "resp_1",
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 1700,
            "input_tokens_details": {"cached_tokens": 1536},
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def chat_payload(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 512},
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }


# --- Reasoning effort ---------------------------------------------------------


def test_openai_reasoning_effort_is_sent_and_recorded() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=responses_payload({"command": {"intent": "PAUSE_SESSION"}}))

    adapter = OpenAITextUnderstanding(
        openai_client(handler), model_name="gpt-5.6-luna", reasoning_effort="none"
    )
    result = adapter.interpret("Jarvis, pause", CONTEXT)

    assert seen["body"]["reasoning"] == {"effort": "none"}
    assert seen["body"]["max_output_tokens"] == 512
    assert "temperature" not in seen["body"]
    assert result.command.intent == "PAUSE_SESSION"
    assert result.metadata["reasoning_effort"] == "none"
    assert result.metadata["max_output_tokens"] == "512"
    assert result.metadata["cached_input_tokens"] == "1536"
    assert result.metadata["reasoning_tokens"] == "0"


def test_openai_without_reasoning_effort_sends_no_reasoning_field() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json=responses_payload({"command": {"intent": "PAUSE_SESSION"}}, reasoning=False)
        )

    result = OpenAITextUnderstanding(openai_client(handler), model_name="gpt-4.1-nano").interpret(
        "Jarvis, pause", CONTEXT
    )
    assert "reasoning" not in seen["body"]
    assert result.metadata["reasoning_effort"] == "provider-default"


def test_reasoning_that_exhausts_the_output_budget_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "reasoning", "id": "rs_1", "summary": []}],
                "usage": {"input_tokens": 1700, "output_tokens": 512},
            },
        )

    adapter = OpenAITextUnderstanding(
        openai_client(handler), model_name="gpt-5.6-terra", reasoning_effort="high"
    )
    with pytest.raises(ModelOutputError, match="completed"):
        adapter.interpret("Jarvis, pause", CONTEXT)


def test_a_provider_rejecting_the_reasoning_value_is_a_clear_configuration_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "unsupported value"}})

    adapter = OpenAITextUnderstanding(
        openai_client(handler), model_name="some-model", reasoning_effort="minimal"
    )
    with pytest.raises(ModelUnavailableError, match="reasoning effort"):
        adapter.interpret("Jarvis, pause", CONTEXT)


def test_compatible_reasoning_effort_is_sent_as_the_chat_field() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat_payload('{"intent":"PAUSE_SESSION"}'))

    adapter = OpenAICompatibleTextUnderstanding(
        compatible_client(handler, base_url="https://llm.example.test/v1"),
        model_name="example",
        reasoning_effort="low",
    )
    result = adapter.interpret("hold on", CONTEXT)
    assert seen["body"]["reasoning_effort"] == "low"
    assert result.metadata["reasoning_effort"] == "low"
    assert result.metadata["cached_input_tokens"] == "512"
    assert result.metadata["reasoning_tokens"] == "3"


def base(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "model_cache_dir": tmp_path / "models",
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'x.db'}",
    }
    values.update(overrides)
    return Settings(**values)


def test_an_unknown_reasoning_effort_is_rejected_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="reasoning_effort"):
        base(tmp_path, hosted_text_reasoning_effort="extreme")


def test_configured_reasoning_effort_reaches_the_hosted_text_adapter(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        text_provider="openai",
        allow_hosted_models=True,
        hosted_text_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_text_model_name="gpt-5.4-nano",
        hosted_text_reasoning_effort="none",
    )
    adapters = build_model_adapters(settings)
    try:
        assert isinstance(adapters.text, CascadeTextUnderstanding)
        model = adapters.text.language_model
        assert isinstance(model, OpenAITextUnderstanding)
        assert model.request_payload("hold on", CONTEXT)["reasoning"] == {"effort": "none"}
    finally:
        adapters.close()


# --- Locality -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "locality"),
    [
        ("http://127.0.0.1:8080/v1", "local"),
        ("http://localhost:1234/v1", "local"),
        ("http://[::1]:8000/v1", "local"),
        ("https://api.deepseek.example.test", "hosted"),
    ],
)
def test_loopback_compatible_endpoints_are_labelled_local(base_url: str, locality: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_payload('{"intent":"PAUSE_SESSION"}'))

    adapter = OpenAICompatibleTextUnderstanding(
        compatible_client(handler, base_url=base_url), model_name="served-model"
    )
    assert adapter.interpret("hold on", CONTEXT).metadata["locality"] == locality


def test_a_loopback_text_server_keeps_the_profile_local(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        text_provider="openai_compatible",
        allow_hosted_models=True,
        hosted_text_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_text_base_url="http://127.0.0.1:8080/v1",
        hosted_text_model_name="served-model",
        hosted_text_provider_label="LM Studio",
    )
    assert settings.stage_locality("text") == "local"
    assert settings.stage_is_hosted("text")  # still the hosted adapter family
    assert not settings.stage_is_remote("text")
    assert not settings.any_stage_remote
    assert settings.execution_profile() == "local"


def test_a_remote_text_server_makes_the_profile_hybrid(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        text_provider="openai_compatible",
        allow_hosted_models=True,
        hosted_text_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_text_base_url="https://llm.example.test/v1",
        hosted_text_model_name="served-model",
    )
    assert settings.stage_locality("text") == "hosted"
    assert settings.any_stage_remote
    assert settings.execution_profile() == "hybrid"


# --- Hosted verifier ------------------------------------------------------------


def test_the_default_verifier_is_still_local_ollama(tmp_path: Path) -> None:
    settings = base(tmp_path, model_mode=ModelMode.LOCAL, text_verifier_model_name="qwen2.5:3b")
    verifier = build_verifier(settings)
    assert isinstance(verifier, OllamaTextUnderstanding)
    assert settings.verifier_locality() == "local"


def test_an_openai_compatible_verifier_is_built_on_its_own_endpoint(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        allow_hosted_models=True,
        text_verifier_model_name="glm-4.5-air",
        text_verifier_provider="openai_compatible",
        text_verifier_base_url="https://verifier.example.test/v1",
        text_verifier_api_key=SecretStr(PLACEHOLDER_KEY),
        text_verifier_provider_label="GLM",
        text_verifier_reasoning_effort="none",
        text_verifier_extra_body='{"thinking": {"type": "disabled"}}',
    )
    adapters = build_model_adapters(settings)
    try:
        assert isinstance(adapters.text, CascadeTextUnderstanding)
        assert isinstance(adapters.text.language_model, OllamaTextUnderstanding)
        verifier = build_verifier(settings)
        assert isinstance(verifier, OpenAICompatibleTextUnderstanding)
        payload = verifier.request_payload("hold on", CONTEXT)
        assert payload["model"] == "glm-4.5-air"
        assert payload["reasoning_effort"] == "none"
        assert payload["thinking"] == {"type": "disabled"}
        assert settings.verifier_label() == "GLM"
        assert settings.verifier_locality() == "hosted"
        assert settings.any_stage_remote  # the verifier sends text off the machine
        assert "verified-by-glm-4.5-air" in adapters.text.model_name
    finally:
        adapters.close()


def test_an_openai_verifier_uses_strict_schema(tmp_path: Path) -> None:
    settings = base(
        tmp_path,
        model_mode=ModelMode.LOCAL,
        allow_hosted_models=True,
        text_verifier_model_name="gpt-5.4-nano",
        text_verifier_provider="openai",
        hosted_api_key=SecretStr(PLACEHOLDER_KEY),
    )
    verifier = build_verifier(settings)
    assert isinstance(verifier, OpenAITextUnderstanding)
    verifier.close()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"text_verifier_provider": "openai_compatible",
             "text_verifier_base_url": "https://v.example.test",
             "text_verifier_api_key": SecretStr(PLACEHOLDER_KEY)},
            "ALLOW_HOSTED_MODELS",
        ),
        (
            {"text_verifier_provider": "openai_compatible", "allow_hosted_models": True,
             "text_verifier_api_key": SecretStr(PLACEHOLDER_KEY)},
            "VERIFIER_BASE_URL",
        ),
        (
            {"text_verifier_provider": "openai", "allow_hosted_models": True},
            "API key",
        ),
        (
            {"text_verifier_provider": "fake", "allow_hosted_models": True},
            "fake provider",
        ),
        (
            {"text_verifier_provider": "openai", "allow_hosted_models": True,
             "hosted_api_key": SecretStr(PLACEHOLDER_KEY),
             "text_verifier_extra_body": '{"model": "other"}'},
            "may not override",
        ),
    ],
)  # fmt: skip
def test_hosted_verifier_configuration_fails_closed(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        base(tmp_path, model_mode=ModelMode.LOCAL, text_verifier_model_name="v", **overrides)


def test_a_disagreeing_hosted_verifier_makes_the_cascade_abstain() -> None:
    def main_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_payload('{"intent":"COMPLETE_STEP"}'))

    def verifier_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses_payload({"command": {"intent": "PAUSE_SESSION"}}))

    model = OpenAICompatibleTextUnderstanding(
        compatible_client(main_handler, base_url="https://main.example.test/v1"),
        model_name="main-model",
    )
    verifier = OpenAITextUnderstanding(
        openai_client(verifier_handler), model_name="gpt-5.4-nano", reasoning_effort="none"
    )
    cascade = CascadeTextUnderstanding(model=model, verifier=verifier)
    result = cascade.interpret("we are all finished with this bit", CONTEXT)

    assert result.metadata["cascade_route"] == "abstain_model_disagreement"
    assert result.command.intent == "UNSUPPORTED"


def test_an_agreeing_hosted_verifier_lets_the_proposal_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_payload('{"intent":"COMPLETE_STEP"}'))

    def verifier_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses_payload({"command": {"intent": "COMPLETE_STEP"}}))

    cascade = CascadeTextUnderstanding(
        model=OpenAICompatibleTextUnderstanding(
            compatible_client(handler, base_url="https://main.example.test/v1"),
            model_name="main-model",
        ),
        verifier=OpenAITextUnderstanding(openai_client(verifier_handler), model_name="v"),
    )
    result = cascade.interpret("we are all finished with this bit", CONTEXT)
    assert result.metadata["cascade_route"] == "llm_verified"
    assert result.command.intent == "COMPLETE_STEP"
