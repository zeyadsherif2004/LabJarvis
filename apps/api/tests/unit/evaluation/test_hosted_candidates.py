"""Hosted evaluation candidates stay opt-in and distinguishable from local ones."""

import pytest
from pydantic import ValidationError

from labjarvis.domain.models import ParsedCommand
from labjarvis.evaluation.run import _text_records
from labjarvis.evaluation.schemas import EvaluationCandidate, EvaluationConfig, EvaluationSample
from labjarvis.ports.models import TextResult
from labjarvis.settings import Settings

HOSTED_CANDIDATE = {
    "id": "openai-text",
    "modality": "text",
    "adapter": "openai",
    "model_name": "gpt-5.6-terra",
    "provider": "openai",
    "execution_location": "remote",
}

LOCAL_CANDIDATE = {
    "id": "ollama-text",
    "modality": "text",
    "adapter": "ollama",
    "model_name": "qwen2.5:1.5b",
}


def config(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "hosted-run",
        "evidence_class": "student_collected",
        "datasets": {"text": "evaluation/datasets/text.jsonl"},
        "candidates": [HOSTED_CANDIDATE],
    }
    base.update(overrides)
    return base


def test_hosted_candidate_records_provider_and_remote_execution() -> None:
    candidate = EvaluationCandidate.model_validate(HOSTED_CANDIDATE)

    assert candidate.adapter == "openai"
    assert candidate.provider == "openai"
    assert candidate.execution_location == "remote"
    assert candidate.model_name == "gpt-5.6-terra"


def test_local_candidates_remain_distinguishable_from_hosted_ones() -> None:
    local = EvaluationCandidate.model_validate(LOCAL_CANDIDATE)

    assert local.execution_location == "local"
    assert local.provider is None


def test_hosted_candidate_must_record_its_provider() -> None:
    with pytest.raises(ValidationError, match="must record the provider"):
        EvaluationCandidate.model_validate({**HOSTED_CANDIDATE, "provider": None})


def test_hosted_candidate_cannot_claim_local_execution() -> None:
    with pytest.raises(ValidationError, match="execution_location 'remote'"):
        EvaluationCandidate.model_validate({**HOSTED_CANDIDATE, "execution_location": "local"})


def test_local_adapter_cannot_claim_remote_execution() -> None:
    with pytest.raises(ValidationError, match="cannot be marked remote"):
        EvaluationCandidate.model_validate({**LOCAL_CANDIDATE, "execution_location": "remote"})


def test_hosted_evaluation_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match="explicit opt-in"):
        EvaluationConfig.model_validate(config())


def test_hosted_evaluation_runs_once_opted_in() -> None:
    parsed = EvaluationConfig.model_validate(config(allow_hosted_candidates=True))

    assert parsed.candidates[0].execution_location == "remote"


def test_local_only_configuration_needs_no_hosted_opt_in() -> None:
    parsed = EvaluationConfig.model_validate(config(candidates=[LOCAL_CANDIDATE]))

    assert parsed.allow_hosted_candidates is False


def test_disabled_hosted_candidate_does_not_force_opt_in() -> None:
    parsed = EvaluationConfig.model_validate(
        config(candidates=[{**HOSTED_CANDIDATE, "enabled": False}, LOCAL_CANDIDATE])
    )

    assert parsed.allow_hosted_candidates is False


def test_synthetic_evidence_remains_ineligible_for_final_claims() -> None:
    with pytest.raises(ValidationError, match="cannot support final claims"):
        EvaluationConfig.model_validate(
            config(
                evidence_class="synthetic_development_only",
                eligible_for_final_claims=True,
                allow_hosted_candidates=True,
            )
        )


def test_text_runner_executes_explicitly_opted_in_openai_candidate(monkeypatch) -> None:
    calls: list[str] = []

    class HostedAdapter:
        def __init__(self, *, model_name: str, settings: Settings) -> None:
            calls.append(model_name)
            assert settings.hosted_api_key is not None

        def interpret(self, transcript, context) -> TextResult:
            del transcript, context
            return TextResult(
                command=ParsedCommand(intent="PAUSE_SESSION", parameters={}, confidence=0.9),
                adapter="openai-responses-json-schema",
                model_name="gpt-5.6-terra",
            )

        def close(self) -> None:
            calls.append("closed")

    monkeypatch.setattr("labjarvis.evaluation.run._openai_text_adapter", HostedAdapter)
    candidate = EvaluationCandidate.model_validate(HOSTED_CANDIDATE)
    sample = EvaluationSample(
        id="text-1",
        modality="text",
        synthetic=True,
        transcript="pause",
        expected_intent="PAUSE_SESSION",
    )
    settings = Settings(
        hosted_api_key="test-only-secret",
        allow_hosted_models=True,
        hosted_text_model_name="gpt-5.6-terra",
        hosted_speech_model_name="gpt-4o-transcribe",
        hosted_vision_model_name="gpt-5.6-terra",
    )

    records = _text_records(candidate, (sample,), settings)

    assert records[0]["intent_correct"] == 1
    assert calls == ["gpt-5.6-terra", "closed"]
