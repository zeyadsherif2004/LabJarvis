import json
from pathlib import Path

from labjarvis.domain.models import ParsedCommand
from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.ports.models import TextResult
from research.post_review.run_interpreters import (
    evaluation_settings,
    evaluate_interpreter,
    load_text_samples,
)


class FixedAdapter:
    adapter_name = "fixed"
    model_name = "fixed-v1"

    def interpret(self, transcript, context) -> TextResult:
        del transcript, context
        return TextResult(
            command=ParsedCommand(
                intent="RECORD_MEASUREMENT",
                parameters={"kind": "temperature", "value": "37", "unit": "degrees Celsius"},
                confidence=0.9,
            ),
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={"request_id": "safe-id", "input_tokens": "12"},
        )


class FailingAdapter:
    adapter_name = "failing"
    model_name = "failing-v1"

    def interpret(self, transcript, context) -> TextResult:
        del transcript, context
        raise ModelUnavailableError("provider unavailable")


def test_load_text_samples_freezes_original_fields_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "text-1",
                "transcript": "record the temperature as 37 degrees Celsius",
                "expected_intent": "RECORD_MEASUREMENT",
                "expected_parameters": {"kind": "temperature", "value": "37", "unit": "degC"},
                "action_valid": True,
                "session_state": "ACTIVE",
                "category": "valid_measurement",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    samples, digest = load_text_samples(path)

    assert len(samples) == 1
    assert samples[0]["id"] == "text-1"
    assert len(digest) == 64


def test_load_text_samples_ignores_grounding_rows_in_mixed_manifest(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    grounding = {
        "id": "g-1",
        "kind": "grounding",
        "source_id": "public-1",
        "question": "What temperature?",
        "expected_supported": True,
        "expected_contains": "42 C",
    }
    command = {
        "id": "c-1",
        "kind": "command",
        "source_id": "public-1",
        "transcript": "pause",
        "expected_intent": "PAUSE_SESSION",
        "expected_parameters": {},
        "action_valid": True,
        "session_state": "ACTIVE",
        "category": "public_protocol",
    }
    path.write_text(
        json.dumps(grounding) + "\n" + json.dumps(command) + "\n",
        encoding="utf-8",
    )

    samples, _ = load_text_samples(path)

    assert [sample["id"] for sample in samples] == ["c-1"]


def test_evaluate_interpreter_records_component_outcomes_and_safe_metadata() -> None:
    samples = [
        {
            "id": "text-1",
            "transcript": "record the temperature as 37 degrees Celsius",
            "expected_intent": "RECORD_MEASUREMENT",
            "expected_parameters": {"kind": "temperature", "value": "37", "unit": "degC"},
            "action_valid": True,
            "session_state": "ACTIVE",
            "category": "valid_measurement",
        }
    ]

    result = evaluate_interpreter(FixedAdapter(), samples)

    assert result["sample_count"] == 1
    assert result["failure_count"] == 0
    assert result["metrics"]["intent_accuracy"] == 1.0
    assert result["metrics"]["exact_command_accuracy"] == 1.0
    assert result["records"][0]["components"]["validation"]["executed"] is True
    assert result["records"][0]["provider_metadata"] == {
        "request_id": "safe-id",
        "input_tokens": "12",
    }


def test_evaluate_interpreter_retains_failures_in_denominator() -> None:
    samples = [
        {
            "id": "text-1",
            "transcript": "pause",
            "expected_intent": "PAUSE_SESSION",
            "expected_parameters": {},
            "action_valid": True,
            "session_state": "ACTIVE",
            "category": "valid_control",
        }
    ]

    result = evaluate_interpreter(FailingAdapter(), samples)

    assert result["sample_count"] == 1
    assert result["failure_count"] == 1
    assert result["metrics"]["intent_accuracy"] == 0.0
    assert result["records"][0]["error_code"] == "MODEL_UNAVAILABLE"


def test_evaluation_settings_accept_key_without_full_hosted_app_profile(monkeypatch) -> None:
    monkeypatch.setenv("LABJARVIS_MODEL_MODE", "hosted")
    monkeypatch.setenv("LABJARVIS_HOSTED_API_KEY", "test-only-secret")
    monkeypatch.delenv("LABJARVIS_HOSTED_TEXT_MODEL_NAME", raising=False)
    monkeypatch.delenv("LABJARVIS_HOSTED_SPEECH_MODEL_NAME", raising=False)
    monkeypatch.delenv("LABJARVIS_HOSTED_VISION_MODEL_NAME", raising=False)

    settings = evaluation_settings()

    assert settings.model_mode.value == "fake"
    assert settings.hosted_api_key is not None
