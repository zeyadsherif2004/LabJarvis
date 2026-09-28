"""The deterministic-first cascade and its agreement/abstention mode."""

from collections.abc import Callable

import pytest

from labjarvis.domain.models import ParsedCommand
from labjarvis.infrastructure.cascade import CascadeTextUnderstanding, commands_agree
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelOutputError,
    ModelUnavailableError,
)
from labjarvis.infrastructure.rule_grammar import (
    RuleGrammarTextUnderstanding,
    parse_rule_command,
)
from labjarvis.ports.models import AdapterReadiness, CommandContext, TextResult

CONTEXT = CommandContext("s", "ACTIVE", "step", "Incubate the tube.")


class ScriptedModel:
    adapter_name = "scripted"
    model_name = "scripted-llm"

    def __init__(self, respond: Callable[[str], ParsedCommand]) -> None:
        self._respond = respond
        self.calls: list[str] = []

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        del context
        self.calls.append(transcript)
        return TextResult(
            command=self._respond(transcript),
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={"confidence_basis": "scripted", "prompt_version": "p"},
        )

    def readiness(self) -> AdapterReadiness:
        return AdapterReadiness(ready=True, adapter="scripted", model_name="m", detail="ok")


class FailingModel(ScriptedModel):
    def __init__(self, error: Exception) -> None:
        super().__init__(lambda _: ParsedCommand("HELP", {}, 0.8))
        self._error = error

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        self.calls.append(transcript)
        raise self._error


def pause(_: str) -> ParsedCommand:
    return ParsedCommand("PAUSE_SESSION", {}, 0.8)


def test_grammar_match_never_calls_the_model_in_rules_first_mode() -> None:
    model = ScriptedModel(pause)
    result = CascadeTextUnderstanding(model=model).interpret(
        "Record the temperature as 37 degrees Celsius", CONTEXT
    )

    assert model.calls == []
    assert result.command.intent == "RECORD_MEASUREMENT"
    assert result.command.confidence == 0.99
    assert result.metadata["cascade_route"] == "rule"
    assert result.metadata["llm_called"] == "false"


def test_unrecognised_paraphrase_is_routed_to_the_model() -> None:
    model = ScriptedModel(pause)
    result = CascadeTextUnderstanding(model=model).interpret("hold on a moment", CONTEXT)

    assert model.calls == ["hold on a moment"]
    assert result.command.intent == "PAUSE_SESSION"
    assert result.metadata["cascade_route"] == "llm"
    assert result.metadata["rule_matched"] == "false"
    assert result.metadata["llm_model"] == "scripted-llm"
    assert "llm_latency_ms" in result.metadata
    assert result.metadata["llm_prompt_version"] == "p"


def test_compound_command_is_refused_by_the_grammar_without_the_model() -> None:
    model = ScriptedModel(pause)
    result = CascadeTextUnderstanding(model=model).interpret("pause and end session", CONTEXT)

    assert model.calls == []
    assert result.command.intent == "UNSUPPORTED"


@pytest.mark.parametrize(
    "error",
    [
        ModelUnavailableError("down"),
        ModelOutputError("invalid"),
        InferenceTimeoutError("slow"),
    ],
)
def test_model_failure_on_the_routed_path_fails_closed(error: Exception) -> None:
    result = CascadeTextUnderstanding(model=FailingModel(error)).interpret(
        "hold on a moment", CONTEXT
    )

    assert result.command.intent == "UNSUPPORTED"
    assert result.command.confidence == 0.0
    assert result.metadata["cascade_route"] == "llm_failed"
    assert result.metadata["llm_failure"]


def test_agreement_mode_confirms_matching_readings() -> None:
    model = ScriptedModel(
        lambda _: ParsedCommand(
            "RECORD_MEASUREMENT", {"kind": "volume", "value": "12.50", "unit": "mL"}, 0.8
        )
    )
    result = CascadeTextUnderstanding(model=model, mode="agreement").interpret(
        "record the volume as 12.5 ml", CONTEXT
    )

    assert result.command.intent == "RECORD_MEASUREMENT"
    assert result.metadata["cascade_route"] == "agree"


def test_agreement_mode_abstains_when_readings_disagree() -> None:
    model = ScriptedModel(
        lambda _: ParsedCommand(
            "RECORD_MEASUREMENT", {"kind": "volume", "value": "50", "unit": "mL"}, 0.8
        )
    )
    result = CascadeTextUnderstanding(model=model, mode="agreement").interpret(
        "record the volume as 5 ml", CONTEXT
    )

    assert result.command.intent == "UNSUPPORTED"
    assert result.command.confidence == 0.0
    assert result.metadata["cascade_route"] == "abstain_disagreement"
    assert result.metadata["disagreement"] == "true"
    assert '"value":"5"' in result.metadata["rule_result"]
    assert '"value":"50"' in result.metadata["llm_result"]


def test_agreement_mode_treats_a_model_refusal_as_disagreement() -> None:
    model = ScriptedModel(lambda _: ParsedCommand("UNSUPPORTED", {}, 0.0))
    result = CascadeTextUnderstanding(model=model, mode="agreement").interpret("pause", CONTEXT)

    assert result.command.intent == "UNSUPPORTED"
    assert result.metadata["cascade_route"] == "abstain_disagreement"


def test_agreement_mode_keeps_the_grammar_when_the_model_is_unavailable() -> None:
    result = CascadeTextUnderstanding(
        model=FailingModel(ModelUnavailableError("down")), mode="agreement"
    ).interpret("pause", CONTEXT)

    assert result.command.intent == "PAUSE_SESSION"
    assert result.metadata["cascade_route"] == "rule_unverified"


def test_commands_agree_uses_deterministic_normalisation() -> None:
    first = ParsedCommand("RECORD_MEASUREMENT", {"kind": "Volume", "value": "4", "unit": "ml"}, 1)
    second = ParsedCommand(
        "RECORD_MEASUREMENT", {"kind": "volume", "value": "4.0", "unit": "mL"}, 1
    )
    other = ParsedCommand("RECORD_MEASUREMENT", {"kind": "volume", "value": "40", "unit": "mL"}, 1)

    assert commands_agree(first, second)
    assert not commands_agree(first, other)
    assert not commands_agree(first, ParsedCommand("PAUSE_SESSION", {}, 1))


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        CascadeTextUnderstanding(model=ScriptedModel(pause), mode="vote")  # type: ignore[arg-type]


def test_rule_grammar_reports_whether_it_matched() -> None:
    assert parse_rule_command("Jarvis, pause").matched
    assert not parse_rule_command("hold on a moment").matched
    result = RuleGrammarTextUnderstanding().interpret("next step", CONTEXT)
    assert result.command.intent == "GET_NEXT_STEP"
    assert result.metadata["cascade_route"] == "rule"


def test_readiness_reports_the_model_stage_honestly() -> None:
    cascade = CascadeTextUnderstanding(model=ScriptedModel(pause))

    assert cascade.readiness().ready is True
    assert "grammar ready" in cascade.readiness().detail
