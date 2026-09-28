import pytest

from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
    ModelInputError,
)
from labjarvis.ports.models import CommandContext, TextResult

CONTEXT = CommandContext(
    session_id="session-1",
    session_state="ACTIVE",
    active_step_id="step-1",
    active_instruction="Add ethanol.",
)


def test_fake_text_extracts_allowlisted_measurement() -> None:
    parsed = FakeTextUnderstanding().interpret(
        "Jarvis, record the temperature as 37 degrees Celsius",
        CONTEXT,
    ).command

    assert parsed.intent == "RECORD_MEASUREMENT"
    assert parsed.parameters == {
        "kind": "temperature",
        "value": "37",
        "unit": "degrees Celsius",
    }
    assert parsed.confidence >= 0.9


def test_fake_text_rejects_multiple_consequential_commands() -> None:
    parsed = FakeTextUnderstanding().interpret("pause and complete the step", CONTEXT).command

    assert parsed.intent == "UNSUPPORTED"
    assert "multiple commands" in parsed.ambiguities


def test_fake_text_rejects_multiple_commands_sharing_the_same_marker() -> None:
    """A repeated marker (two "record " clauses) must still trip the guard.

    Regression test: the guard used to count distinct markers present rather
    than occurrences, so this utterance slipped through and the greedy
    measurement regex absorbed the observation clause into a garbled
    RECORD_MEASUREMENT (research/results/raw/e2-text-results.json,
    sample text-076).
    """
    parsed = FakeTextUnderstanding().interpret(
        "record an observation as the solution is clear and record the ph as 7 ph",
        CONTEXT,
    ).command

    assert parsed.intent == "UNSUPPORTED"
    assert "multiple commands" in parsed.ambiguities


def test_fake_text_returns_unsupported_instead_of_guessing() -> None:
    parsed = FakeTextUnderstanding().interpret("make everything perfect", CONTEXT).command

    assert parsed.intent == "UNSUPPORTED"
    assert parsed.parameters == {}


@pytest.mark.parametrize(
    ("transcript", "intent"),
    [
        ("what is the current step", "GET_CURRENT_STEP"),
        ("what is the next step", "GET_NEXT_STEP"),
        ("repeat instruction", "REPEAT_INSTRUCTION"),
        ("verify reagent", "VERIFY_REAGENT"),
        ("help", "HELP"),
        ("correct previous entry", "CORRECT_PREVIOUS_ENTRY"),
        ("end session", "END_SESSION"),
    ],
)
def test_fake_text_supports_bounded_guidance_and_session_intents(
    transcript: str,
    intent: str,
) -> None:
    parsed = FakeTextUnderstanding().interpret(transcript, CONTEXT).command

    assert parsed.intent == intent
    assert parsed.confidence >= 0.9


def test_fake_text_parses_a_bounded_timer_duration() -> None:
    parsed = FakeTextUnderstanding().interpret("set a timer for 5 minutes", CONTEXT).command

    assert parsed.intent == "SET_TIMER"
    assert parsed.parameters == {"duration_seconds": "300"}


def test_fake_text_accepts_a_configured_optional_wake_phrase() -> None:
    parsed = (
        FakeTextUnderstanding(wake_phrase="Computer").interpret("Computer, pause", CONTEXT).command
    )

    assert parsed.intent == "PAUSE_SESSION"


def test_fake_text_adapter_returns_a_text_result_carrying_provenance() -> None:
    result = FakeTextUnderstanding().interpret("pause", CONTEXT)

    assert isinstance(result, TextResult)
    assert result.command.intent == "PAUSE_SESSION"
    assert result.adapter == "deterministic-rules"
    assert result.model_name == "fake-text-rules-v1"


def test_text_result_metadata_is_immutable() -> None:
    result = FakeTextUnderstanding().interpret("pause", CONTEXT)

    with pytest.raises(TypeError):
        result.metadata["injected"] = "value"  # type: ignore[index]


def test_fake_audio_and_label_require_explicit_fixture_prefix() -> None:
    speech = FakeSpeechToText().transcribe(
        b"FAKE:record an observation as clear",
        content_type="audio/webm",
    )
    label = FakeLabelRecognition().recognise(b"FAKE:ethanol", content_type="image/jpeg")

    assert speech.transcript == "record an observation as clear"
    assert label.visible_text == "ethanol"

    with pytest.raises(ModelInputError, match="FAKE"):
        FakeSpeechToText().transcribe(b"real media", content_type="audio/webm")
    with pytest.raises(ModelInputError, match="FAKE"):
        FakeLabelRecognition().recognise(b"real image", content_type="image/jpeg")
